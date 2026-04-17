# routes/user_monthly_tracker.py

from flask import Blueprint, request
from config import get_db_connection
from utils.response import api_response
from datetime import datetime, timedelta

user_monthly_tracker_bp = Blueprint("user_monthly_tracker", __name__)

# task_work_tracker.date_time is TEXT like "YYYY-MM-DD HH:MM:SS"
TRACKER_DT = "CAST(twt.date_time AS DATETIME)"
TRACKER_YEAR_MONTH = f"(YEAR({TRACKER_DT})*100 + MONTH({TRACKER_DT}))"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def month_year_to_yyyymm_sql(month_year_col: str) -> str:
    """
    Your DB stores month_year like 'JAN2026', 'DEC2025' (MONYYYY).
    Convert MONYYYY -> integer YYYYMM inside SQL.
    """
    return f"""
    CAST(
      DATE_FORMAT(
        STR_TO_DATE(CONCAT('01-', {month_year_col}), '%d-%b%Y'),
        '%Y%m'
      ) AS UNSIGNED
    )
    """


# ---------------------------
# Single helper (role_name + agent_role_id)
# ---------------------------
def get_role_context(cursor, user_id: int) -> dict:
    """
    Returns:
      {
        "user_role_id": int|None,
        "user_role_name": str,
        "agent_role_id": int|None
      }
    """
    cursor.execute(
        """
        SELECT
            u.role_id AS user_role_id,
            r.role_name AS user_role_name,
            (
                SELECT ur2.role_id
                FROM user_role ur2
                WHERE LOWER(TRIM(ur2.role_name)) = 'agent'
                LIMIT 1
            ) AS agent_role_id
        FROM tfs_user u
        JOIN user_role r ON r.role_id = u.role_id
        WHERE u.user_id=%s AND u.is_active=1 AND u.is_delete=1
        """,
        (int(user_id),),
    )
    row = cursor.fetchone() or {}
    return {
        "user_role_id": row.get("user_role_id"),
        "user_role_name": (row.get("user_role_name") or "").strip().lower(),
        "agent_role_id": row.get("agent_role_id"),
    }


# ---------------------------
# UPDATE
# ---------------------------
@user_monthly_tracker_bp.route("/update", methods=["POST"])
def update_user_monthly_target():
    data = request.get_json(silent=True) or {}

    # Required fields: user_id and month_year
    user_id = data.get("user_id")
    month_year = data.get("month_year")
    extra_assigned_hours = data.get("extra_assigned_hours")

    if not user_id or not month_year:
        return api_response(400, "user_id and month_year are required")

    if extra_assigned_hours is None:
        return api_response(400, "extra_assigned_hours is required")

    # Only extra_assigned_hours is allowed for update
    allowed_fields = ["extra_assigned_hours"]
    
    # Validate only allowed fields are provided
    invalid_fields = []
    for key in data.keys():
        if key not in allowed_fields and key not in ["user_id", "month_year"]:
            invalid_fields.append(key)
    
    if invalid_fields:
        return api_response(400, f"Invalid fields: {', '.join(invalid_fields)}. Only extra_assigned_hours is allowed for update")

    user_id = int(user_id)
    month_year = str(month_year).strip()  # MONYYYY like JAN2026
    extra_assigned_hours = int(extra_assigned_hours)
    created_date = str(data.get("created_date") or now_str())

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # ---- Validate user exists
        cursor.execute(
            """
            SELECT user_id
            FROM tfs_user
            WHERE user_id=%s AND is_delete=1
            """,
            (user_id,),
        )
        if not cursor.fetchone():
            return api_response(404, "User not found or inactive")

        # ---- Conditional logic based on date
        # Convert month_year to date for comparison
        try:
            month_date = datetime.strptime(month_year, '%b%Y')  # APR2025 -> 2025-04-01
            cutoff_date = datetime(2025, 4, 1)  # April 2025 cutoff
            
            if month_date >= cutoff_date:
                # Use rosters table for April 2025 onwards
                cursor.execute(
                    """
                    SELECT roster_id, base_target, extra_assigned_hours
                    FROM rosters
                    WHERE user_id=%s AND month_year=%s
                    """,
                    (user_id, month_year),
                )
                existing_roster = cursor.fetchone()
                
                if existing_roster:
                    # Update existing roster
                    new_final_target = (existing_roster['base_target'] or 0) + extra_assigned_hours
                    cursor.execute(
                        """
                        UPDATE rosters
                        SET extra_assigned_hours=%s, updated_at=%s, final_target=%s
                        WHERE user_id=%s AND month_year=%s
                        """,
                        (extra_assigned_hours, created_date, new_final_target, user_id, month_year),
                    )
                    message = "Roster updated successfully"
                else:
                    # Insert new roster record
                    cursor.execute(
                        """
                        INSERT INTO rosters
                            (user_id, month_year, extra_assigned_hours, final_target, is_active, created_date)
                        VALUES (%s, %s, %s, %s, 1, %s)
                        """,
                        (
                            user_id,
                            month_year,
                            extra_assigned_hours,
                            extra_assigned_hours,  # final_target = extra_assigned_hours (base_target = 0 initially)
                            created_date,
                        ),
                    )
                    message = "Roster created successfully"
            else:
                # Use user_monthly_tracker table for before April 2025 (historical data)
                cursor.execute(
                    """
                    SELECT user_monthly_tracker_id
                    FROM user_monthly_tracker
                    WHERE user_id=%s AND month_year=%s AND is_active=1
                    """,
                    (user_id, month_year),
                )
                existing_tracker = cursor.fetchone()
                
                if existing_tracker:
                    # Update existing tracker
                    cursor.execute(
                        """
                        UPDATE user_monthly_tracker
                        SET extra_assigned_hours=%s
                        WHERE user_id=%s AND month_year=%s
                        """,
                        (extra_assigned_hours, user_id, month_year),
                    )
                    message = "Monthly tracker updated successfully"
                else:
                    # Insert new tracker record
                    cursor.execute(
                        """
                        INSERT INTO user_monthly_tracker
                            (user_id, month_year, extra_assigned_hours, is_active, created_date)
                        VALUES (%s, %s, %s, 1, %s)
                        """,
                        (
                            user_id,
                            month_year,
                            extra_assigned_hours,
                            created_date,
                        ),
                    )
                    message = "Monthly tracker created successfully"
        except ValueError:
            # Invalid date format, use user_monthly_tracker as fallback
            cursor.execute(
                """
                SELECT user_monthly_tracker_id
                FROM user_monthly_tracker
                WHERE user_id=%s AND month_year=%s 
                """,
                (user_id, month_year),
            )
            existing_tracker = cursor.fetchone()
            
            if existing_tracker:
                # Update existing tracker
                cursor.execute(
                    """
                    UPDATE user_monthly_tracker
                    SET extra_assigned_hours=%s
                    WHERE user_id=%s AND month_year=%s
                    """,
                    (extra_assigned_hours, user_id, month_year),
                )
                message = "Monthly tracker updated successfully"
            else:
                # Insert new tracker record
                cursor.execute(
                    """
                    INSERT INTO user_monthly_tracker
                        (user_id, month_year, extra_assigned_hours, is_active, created_date)
                    VALUES (%s, %s, %s, 1, %s)
                    """,
                    (
                        user_id,
                        month_year,
                        extra_assigned_hours,
                        created_date,
                    ),
                )
                message = "Monthly tracker created successfully"

        conn.commit()
        return api_response(200, message)

    except Exception as e:
        conn.rollback()
        return api_response(500, f"Update failed: {str(e)}")
    finally:
        cursor.close()
        conn.close()


# ---------------------------
# DELETE (SOFT)
# ---------------------------
@user_monthly_tracker_bp.route("/delete", methods=["POST"])
def delete_user_monthly_target():
    data = request.get_json(silent=True) or {}

    if not data.get("user_monthly_tracker_id"):
        return api_response(400, "user_monthly_tracker_id is required")

    umt_id = int(data["user_monthly_tracker_id"])

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            delete from user_monthly_tracker
            WHERE user_monthly_tracker_id=%s AND is_active=1
            """,
            (umt_id,),
        )
        conn.commit()

        if cursor.rowcount == 0:
            return api_response(404, "Active record not found")

        return api_response(200, "User monthly target deleted successfully")

    except Exception as e:
        conn.rollback()
        return api_response(500, f"Delete failed: {str(e)}")
    finally:
        cursor.close()
        conn.close()


# ---------------------------
# LIST
# Changes:
# - month_year optional: if missing -> show ALL data (no month filtering)
# - if month_year provided -> show only that specific month data
# - only agent rows (managers/qa won't appear as rows)
# - monthly_total_target = monthly_target + extra_assigned_hours
# - pending_days = working_days(from UMT) - distinct worked days till today (month-wise)
# - do NOT return working_days or working_days_till_today separately
# ---------------------------
@user_monthly_tracker_bp.route("/list", methods=["POST"])
def list_user_monthly_targets():
    data = request.get_json(silent=True) or {}

    logged_in_user_id = data.get("logged_in_user_id")
    month_year = (data.get("month_year") or "").strip()  # OPTIONAL (MonYYYY)
    filter_user_id = data.get("user_id")  # OPTIONAL
    filter_team_id = data.get("team_id")  # OPTIONAL

    if not logged_in_user_id:
        return api_response(400, "logged_in_user_id is required", None)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        ctx = get_role_context(cursor, int(logged_in_user_id))
        my_role_name = (ctx.get("user_role_name") or "").lower()
        agent_role_id = ctx.get("agent_role_id")

        if not agent_role_id:
            return api_response(500, "Agent role not found in user_role table", None)
        
        if month_year:
            dt = datetime.strptime(month_year, "%b%Y")  # Mar2026
            month_start = dt.replace(day=1)
            month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(seconds=1)
            month_start_str = month_start.strftime("%Y-%m-%d %H:%M:%S")
            month_end_str = month_end.strftime("%Y-%m-%d %H:%M:%S")
        else:
            # When no month_year provided, don't filter by date range
            month_start_str = None
            month_end_str = None

        # ---------------- Base WHERE: only agent rows ----------------
        if month_year:
            user_where = """
                WHERE u.is_delete=1
                AND u.role_id=%s
                AND (
                        u.is_active = 1
                        OR (
                            u.is_active = 0
                            AND u.deactivated_at IS NOT NULL
                            AND u.deactivated_at BETWEEN %s AND %s
                        )
                )
            """
            user_params = [agent_role_id, month_start_str, month_end_str]
        else:
            user_where = """
                WHERE u.is_delete=1
                AND u.role_id=%s
                AND (
                        u.is_active = 1
                        OR (
                            u.is_active = 0
                            AND u.deactivated_at IS NOT NULL
                        )
                )
            """
            user_params = [agent_role_id]

        if filter_user_id:
            user_where += " AND u.user_id=%s"
            user_params.append(int(filter_user_id))

        if filter_team_id:
            user_where += " AND u.team_id=%s"
            user_params.append(int(filter_team_id))

        if my_role_name in ("admin", "super admin"):
            pass
        elif my_role_name == "agent":
            user_where += " AND u.user_id=%s"
            user_params.append(int(logged_in_user_id))
        else:
            mid = str(logged_in_user_id)
            user_where += """
                AND (
                    JSON_CONTAINS(u.project_manager_id, %s)
                    OR JSON_CONTAINS(u.asst_manager_id, %s)
                    OR JSON_CONTAINS(u.qa_id, %s)
                )
            """

            user_params.extend([str(mid), str(mid), str(mid)])

        # ---------------- Joins: month_year optional ----------------
        # temp_qc.date is TEXT 'YYYY-MM-DD'
        QC_YEAR_MONTH = "DATE_FORMAT(STR_TO_DATE(tq.date, '%Y-%m-%d'), '%Y%m')"

        if month_year:
            umt_join = """
                INNER JOIN (
                    -- Hybrid approach: Use rosters for April 2025 onwards, user_monthly_tracker for before April
                    SELECT 
                        user_id,
                        month_year,
                        working_days,
                        final_target as monthly_target,
                        extra_assigned_hours,
                        roster_id as user_monthly_tracker_id,
                        'roster' as source_table
                    FROM rosters 
                    WHERE month_year >= 'APR2025'  -- April 2025 onwards
                    UNION ALL
                    SELECT 
                        user_id,
                        month_year,
                        working_days,
                        monthly_target,
                        extra_assigned_hours,
                        user_monthly_tracker_id,
                        'user_monthly_tracker' as source_table
                    FROM user_monthly_tracker
                    WHERE is_active=1 AND month_year < 'APR2025'   -- Before April 2025
                ) umt
                  ON umt.user_id = u.user_id
                 AND umt.month_year=%s
            """
            twt_join = f"""
                LEFT JOIN task_work_tracker twt
                  ON twt.user_id = u.user_id
                 AND twt.is_active=1
                 AND {TRACKER_YEAR_MONTH} = {month_year_to_yyyymm_sql('%s')}
            """
            # ✅ avg_qc_score = SUM(qc_score) / COUNT(days having qc_score)
            qc_join = f"""
                LEFT JOIN (
                    SELECT
                        tq.user_id,
                        ROUND(SUM(tq.qc_score) / NULLIF(COUNT(DISTINCT tq.date), 0), 2) AS avg_qc_score,
                        COUNT(DISTINCT tq.date) AS qc_days_count
                    FROM temp_qc tq
                    WHERE tq.qc_score IS NOT NULL
                      AND {QC_YEAR_MONTH} = {month_year_to_yyyymm_sql('%s')}
                    GROUP BY tq.user_id
                  ON twt.user_id = u.user_id
                 AND twt.is_active=1
            """
            qc_join = """
                LEFT JOIN (
                    SELECT
                        tq.user_id,
                        ROUND(SUM(tq.qc_score) / NULLIF(COUNT(DISTINCT tq.date), 0), 2) AS avg_qc_score,
                        COUNT(DISTINCT tq.date) AS qc_days_count
                    FROM temp_qc tq
                    WHERE tq.qc_score IS NOT NULL
                    GROUP BY tq.user_id
                ) qc ON qc.user_id = u.user_id
            """

        # ---------------- Main query ----------------
        query = f"""
            SELECT
                u.user_id,
                u.user_name,
                t.team_name,
                umt.user_monthly_tracker_id,
                umt.month_year,
                umt.working_days,
                COALESCE(CAST(umt.monthly_target AS DECIMAL(10,2)), 0) AS monthly_target,
                COALESCE(umt.extra_assigned_hours, 0) AS extra_assigned_hours,
                (
                    COALESCE(CAST(umt.monthly_target AS DECIMAL(10,2)), 0)
                    + COALESCE(umt.extra_assigned_hours, 0)
                ) AS monthly_total_target,

                COALESCE(SUM(twt.billable_hours), 0) AS total_billable_hours,
                COALESCE(SUM(twt.production), 0) AS total_production,
                COUNT(twt.tracker_id) AS tracker_rows,

                -- QC monthly avg and qc-days count
                qc.avg_qc_score AS avg_qc_score,
                COALESCE(qc.qc_days_count, 0) AS qc_days_count,

                GREATEST(
                    (
                        COALESCE(CAST(umt.monthly_target AS DECIMAL(10,2)), 0)
                        + COALESCE(umt.extra_assigned_hours, 0)
                    ) - COALESCE(SUM(twt.billable_hours), 0),
                    0
                ) AS pending_target
            FROM tfs_user u
            LEFT JOIN team t ON u.team_id = t.team_id
            {umt_join}
            {twt_join}
            {qc_join}
            {user_where}
            GROUP BY
                u.user_id,
                u.user_name,
                t.team_name,
                umt.user_monthly_tracker_id,
                umt.month_year,
                umt.working_days,
                monthly_target,
                extra_assigned_hours,
                qc.avg_qc_score,
                qc.qc_days_count
            ORDER BY u.user_name ASC
        """

        # Params order:
        # if month_year: umt_join(%s), twt_join(%s), qc_join(%s), then user_where params
        if month_year:
            final_params = [month_year, month_year, month_year]
        else:
            final_params = []
        final_params.extend(user_params)

        cursor.execute(query, tuple(final_params))
        rows = cursor.fetchall()
        return api_response(200, "User monthly targets fetched successfully", rows)

    except Exception as e:
        return api_response(500, f"List failed: {str(e)}", None)

    finally:
        cursor.close()
        conn.close()