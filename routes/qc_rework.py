from flask import Blueprint, request
from utils.response import api_response
from config import get_db_connection
from utils.cloudinary_utils import upload_to_cloudinary, FOLDER_QC_REWORK
from datetime import datetime

qc_rework_bp = Blueprint("qc_rework", __name__)

@qc_rework_bp.route("/add_rework_file", methods=["POST"])
def add_rework_file():
    form = request.form
    tracker_id = form.get("tracker_id")

    if not tracker_id:
        return api_response(400, "tracker_id is required")

    uploaded_file = request.files.get("rework_file_path")
    if not uploaded_file or not uploaded_file.filename:
        return api_response(400, "rework_file is required")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Get project and user details to build a descriptive filename
        cursor.execute("""
            SELECT p.project_code, t.task_name, u.user_name
            FROM task_work_tracker twt
            JOIN project p ON twt.project_id = p.project_id
            JOIN task t ON twt.task_id = t.task_id
            JOIN tfs_user u ON twt.user_id = u.user_id
            WHERE twt.tracker_id = %s
        """, (tracker_id,))
        tracker_info = cursor.fetchone()

        if not tracker_info:
            return api_response(404, "Tracker details not found for filename generation")

        project_code = tracker_info.get("project_code", "PROJECT")
        task_name = tracker_info.get("task_name", "TASK")
        user_name = tracker_info.get("user_name", "USER")

        # Build a unique and descriptive filename
        now = datetime.now()
        date_part = now.strftime("%d-%b-%Y")
        time_part = now.strftime("%I%p")
        original_filename = uploaded_file.filename
        ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'file'
        
        # Sanitize parts for filename
        clean_project_code = "".join(c if c.isalnum() else "_" for c in project_code)
        clean_task_name = "".join(c if c.isalnum() else "_" for c in task_name)
        clean_user_name = "".join(c if c.isalnum() else "_" for c in user_name)

        custom_filename = f"{clean_project_code}_{clean_task_name}_{clean_user_name}_{date_part}_{time_part}_rework.{ext}"

        # Upload to Cloudinary
        try:
            cloudinary_url, _ = upload_to_cloudinary(
                uploaded_file,
                FOLDER_QC_REWORK,
                display_name=custom_filename,
                resource_type="raw"
            )
        except Exception as e:
            return api_response(500, f"File upload to Cloudinary failed: {str(e)}")

        # Update the database with the Cloudinary URL
        query = """
            UPDATE qc_rework_tracker
            SET rework_file_path = %s,
                timestamp = %s
            WHERE tracker_id = %s
        """
        
        updated_at = now.strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(query, (cloudinary_url, updated_at, tracker_id))
        conn.commit()

        if cursor.rowcount == 0:
            # This case might happen if the tracker_id exists but is not in qc_rework_tracker yet.
            # Depending on business logic, you might want to INSERT instead.
            # For now, we assume the record is pre-existing.
            return api_response(404, "No rework record found for this tracker_id. Please ensure it is marked for rework first.")

        return api_response(200, "Rework file uploaded and path updated successfully", {"rework_file_path": cloudinary_url})

    except Exception as e:
        conn.rollback()
        return api_response(500, f"Failed to update rework file path: {str(e)}")

    finally:
        cursor.close()
        conn.close()


from flask import Blueprint, request
from utils.response import api_response
from config import get_db_connection

rework_bp = Blueprint("rework", __name__)

@qc_rework_bp.route("/view_rework_trackers", methods=["POST"])
def view_rework_trackers():

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        query = """
        SELECT
            qrt.id,
            u.user_name AS agent_name,
            qrt.timestamp AS worked_datetime,
            qr.timestamp AS evaluation_datetime,
            p.project_name,
            t.task_name,
            qr.status,
            qr.qc_score,
            qrt.rework_file_path

        FROM qc_rework_tracker qrt

        LEFT JOIN tfs_user u 
            ON u.user_id = qrt.agent_id

        LEFT JOIN qc_records qr 
            ON qr.tracker_id = qrt.tracker_id

        LEFT JOIN project p 
            ON p.project_id = qrt.project_id

        LEFT JOIN task t 
            ON t.task_id = qrt.task_id

        ORDER BY qrt.id DESC
        """

        cursor.execute(query)
        records = cursor.fetchall()

        return api_response(
            200,
            "Rework tracker records fetched successfully",
            {
                "count": len(records),
                "records": records
            }
        )

    except Exception as e:
        return api_response(500, f"Failed to fetch records: {str(e)}")

    finally:
        cursor.close()
        conn.close()