from flask import Blueprint, request
from config import get_db_connection
from utils.response import api_response
from datetime import datetime

qa_agent_report_bp = Blueprint("qa_agent_report", __name__)


@qa_agent_report_bp.route("/billable_report", methods=["POST"])
def qa_agent_report():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        data = request.json
        logged_in_user_id = data.get("logged_in_user_id")

        if not logged_in_user_id:
            return api_response(400, "logged_in_user_id is required")

        # Get role for filtering
        cursor.execute("""
            SELECT ur.role_name
            FROM tfs_user u
            JOIN user_role ur ON u.role_id = ur.role_id
            WHERE u.user_id = %s
        """, (logged_in_user_id,))
        user = cursor.fetchone()

        if not user:
            return api_response(404, "User not found")

        role = user["role_name"].strip().lower()

        # Date filters
        date_from = data.get("date_from")
        date_to = data.get("date_to")
        team_id = data.get("team_id")

        # Base query for QA agent report (date-wise)
        base_query = """
        SELECT 
            qa.user_id AS qa_agent_id,
            qa.user_name AS qa_agent_name,
            qa.user_email AS qa_agent_email,
            qa.user_tenure,
            t.team_id,
            t.team_name,
            DATE(qr.date_of_file_submission) AS report_date,
            
            -- Daily QC Records Count
            COUNT(DISTINCT qr.id) AS daily_qc_records,
            
            -- Daily Billable Hours Calculation for QA (target doubled)
            SUM(
                CASE 
                    WHEN tk.qc_percentage > 0 AND qa.user_tenure > 0 THEN
                        (tk.qc_percentage / (qa.user_tenure * 2))  -- Doubled target for QA
                    ELSE 0
                END
            ) AS daily_billable_hours,
            
            -- Daily Production
            COALESCE(SUM(tk.qc_percentage), 0) AS daily_production,
            
            -- Daily Tenure Target (doubled for QA)
            COALESCE(qa.user_tenure * 2, 0) AS daily_qa_user_tenure,
            
            -- Daily QC Score Average
            COALESCE(AVG(qr.qc_score), 0) AS daily_avg_qc_score
            
        FROM qc_records qr
        LEFT JOIN tfs_user qa ON qa.user_id = qr.qa_user_id
        LEFT JOIN task_work_tracker twt ON qr.tracker_id = twt.tracker_id
        LEFT JOIN task tk ON twt.task_id = tk.task_id
        LEFT JOIN team t ON qa.team_id = t.team_id
        WHERE qa.user_id IS NOT NULL
        """

        params = []

        # Date filtering
        if date_from:
            base_query += " AND DATE(qr.date_of_file_submission) >= %s"
            params.append(date_from)

        if date_to:
            base_query += " AND DATE(qr.date_of_file_submission) <= %s"
            params.append(date_to)

        # Team filtering
        if team_id:
            base_query += " AND qa.team_id = %s"
            params.append(team_id)

        # Role-based filtering
        if "admin" in role:
            pass  # Admin can see all
        elif "project manager" in role:
            base_query += " AND (JSON_CONTAINS(qa.project_manager_id, %s) OR qa.user_id = %s)"
            params.extend([f"{logged_in_user_id}", logged_in_user_id])
        elif "assistant manager" in role:
            base_query += " AND (JSON_CONTAINS(qa.asst_manager_id, %s) OR qa.user_id = %s)"
            params.extend([f"{logged_in_user_id}", logged_in_user_id])
        else:
            # QA agents can only see their own records
            base_query += " AND qa.user_id = %s"
            params.append(logged_in_user_id)

        base_query += """
        GROUP BY qa.user_id, qa.user_name, qa.user_email, t.team_id, t.team_name, DATE(qr.date_of_file_submission)
        ORDER BY qa.user_name ASC, report_date DESC
        """

        print("QA Report Query:", base_query)
        print("Params:", params)

        cursor.execute(base_query, tuple(params))
        qa_agents = cursor.fetchall()

        if not qa_agents:
            return api_response(200, "No QA agent records found", {"count": 0, "records": []})

        # Calculate additional metrics for daily records
        for record in qa_agents:
            # Calculate daily efficiency (billable_hours vs available hours)
            record["efficiency_percentage"] = 0
            if record["daily_qa_user_tenure"] > 0:
                record["efficiency_percentage"] = round((record["daily_billable_hours"] / record["daily_qa_user_tenure"]) * 100, 2)

            # Format date
            if record["report_date"]:
                record["report_date"] = record["report_date"].strftime("%Y-%m-%d")

            # Round numeric values
            record["daily_billable_hours"] = round(record["daily_billable_hours"], 2)
            record["daily_production"] = round(record["daily_production"], 2)
            record["daily_qa_user_tenure"] = round(record["daily_qa_user_tenure"], 2)
            record["daily_avg_qc_score"] = round(record["daily_avg_qc_score"], 2)

        # Summary statistics for date-wise data
        unique_agents = len(set(record["qa_agent_id"] for record in qa_agents))
        total_qc_records = sum(record["daily_qc_records"] for record in qa_agents)
        total_billable_hours = sum(record["daily_billable_hours"] for record in qa_agents)
        total_production = sum(record["daily_production"] for record in qa_agents)
        avg_qc_score = sum(record["daily_avg_qc_score"] for record in qa_agents) / len(qa_agents) if qa_agents else 0

        summary = {
            "total_unique_qa_agents": unique_agents,
            "total_daily_records": len(qa_agents),
            "total_qc_records": total_qc_records,
            "total_billable_hours": round(total_billable_hours, 2),
            "total_production": round(total_production, 2),
            "average_qc_score": round(avg_qc_score, 2),
            "date_range": {
                "from": date_from,
                "to": date_to
            }
        }

        return api_response(
            200,
            "QA agent date-wise report fetched successfully",
            {
                "count": len(qa_agents),
                "summary": summary,
                "records": qa_agents
            }
        )

    except Exception as e:
        return api_response(500, f"Failed to fetch QA agent report: {str(e)}")

    finally:
        cursor.close()
        conn.close()
