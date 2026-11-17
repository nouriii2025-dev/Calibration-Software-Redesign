from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from django.conf import settings
from django.http import FileResponse
from decimal import Decimal
import os
import win32com.client
from datetime import date, datetime


EXCEL_HEADERS = [
    "Scope of Work",
    "Datasheet",
    "Job No",
    "Cert. No",
    "Customer name",
    "Address",
    "Tel",
    "FAX",
    "PO",
    "Instrument",
    "Range",
    "Model",
    "Make",
    "Serial",
    "Cal by",
    "Cal. Date",
    "Due Date",
    "Resolution",
    "Accuracy",
    "Tag",
    "Location",
    "Temp",
    "Rel. Humidity",
    "Atm. Press",
    "Ratio",
    "Issue Date",
    "Remarks Status",
]


EXCEL_DIR = Path(settings.MEDIA_ROOT) / "exports"
EXCEL_FILE = EXCEL_DIR / "calibration_register.xlsx"


def get_excel_file():
    """
    Create the master Excel file if it does not already exist.
    """

    EXCEL_DIR.mkdir(parents=True, exist_ok=True)

    if not EXCEL_FILE.exists():

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Calibration Register"

        for column, header in enumerate(EXCEL_HEADERS, start=1):

            cell = worksheet.cell(
                row=1,
                column=column,
                value=header
            )

            cell.font = Font(
                bold=True,
                name="Arial"
            )

            cell.alignment = Alignment(
                horizontal="center",
                vertical="center"
            )

            cell.fill = PatternFill(
                fill_type="solid",
                fgColor="9DC3E6"
            )

        worksheet.freeze_panes = "A2"

        # Give the columns reasonable widths.
        widths = [
            20, 18, 14, 14, 25, 30, 15, 15,
            15, 25, 20, 20, 20, 20, 20, 15,
            15, 15, 15, 18, 18, 15, 18, 18,
            15, 15, 20
        ]

        for index, width in enumerate(widths, start=1):
            worksheet.column_dimensions[
                worksheet.cell(row=1, column=index).column_letter
            ].width = width

        workbook.save(EXCEL_FILE)

    return EXCEL_FILE


def get_certificate_status(certificate):
    """
    Determine the overall certificate status from its result rows.
    """

    results = certificate.results.all()

    if not results.exists():
        return ""

    remarks = []

    for result in results:

        value = (result.remarks or "").strip().upper()

        if value:
            remarks.append(value)

    if not remarks:
        return ""

    if "FAIL" in remarks:
        return "FAIL"

    if all(value == "PASS" for value in remarks):
        return "PASS"

    return ", ".join(sorted(set(remarks)))


def certificate_to_excel_row(certificate):
    """
    Convert one Django Certificate into one Excel row.
    """

    job = certificate.job
    line_item = certificate.line_item

    instrument = ""
    assigned_technician = ""

    if line_item:

        if line_item.instrument:
            instrument = str(line_item.instrument)

        if line_item.assigned_to:
            assigned_technician = str(
                line_item.assigned_to
            )

    # ---------------------------------------------------------
    # Range
    # ---------------------------------------------------------

    range_value = ""

    if line_item:

        if (
            line_item.range_from is not None
            and line_item.range_to is not None
        ):
            range_value = (
                f"{line_item.range_from:g} "
                f"to "
                f"{line_item.range_to:g}"
            )

        elif line_item.range_to is not None:
            range_value = str(line_item.range_to)

        elif line_item.range_from is not None:
            range_value = str(line_item.range_from)

    # ---------------------------------------------------------
    # Accuracy
    # ---------------------------------------------------------

    accuracy = ""

    if certificate.device_accuracy:

        accuracy = str(
            certificate.device_accuracy
        )

        if certificate.device_accuracy_unit:
            accuracy += f" {certificate.device_accuracy_unit}"

    status = get_certificate_status(certificate)

    return [
        job.scope_of_work,                      # Scope of Work
        certificate.datasheet_number,           # Datasheet
        job.job_number,                         # Job No
        certificate.number,                     # Cert. No
        job.customer_name,                      # Customer name
        job.customer_address,                   # Address
        job.telephone_number,                   # Tel
        job.fax_number,                         # FAX
        job.po_number,                          # PO
        instrument,                             # Instrument
        range_value,                            # Range
        line_item.model if line_item else "",   # Model
        "",                                     # Make
        certificate.device_serial,              # Serial
        certificate.calibrated_by,              # Cal by
        certificate.calibration_date,           # Cal. Date
        certificate.re_calibration_date,        # Due Date
        certificate.device_resolution,          # Resolution
        accuracy,                               # Accuracy
        certificate.device_tag_number,          # Tag
        job.location,                           # Location
        certificate.lab_temperature,            # Temp
        certificate.lab_humidity,               # Rel. Humidity
        certificate.ambient_pressure,           # Atm. Press
        round(certificate.device_ratio_value, 3) if certificate.device_ratio_value is not None else None,  # Ratio
        certificate.issue_date,                 # Issue Date
        status,                                 # Remarks Status
    ]



def export_certificate_to_excel(certificate):
    """
    Export/update one certificate in the master Excel workbook.

    Behaviour:
    - Creates the master workbook if it does not exist.
    - Opens the workbook automatically in Microsoft Excel.
    - If the workbook is already open, updates that same open workbook.
    - If Excel/workbook was closed, opens the existing workbook again.
    - Re-exporting the same certificate updates its existing row.
    - Does NOT download a file.
    """

    excel_file = get_excel_file()

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()

    try:
        target_path = os.path.normcase(
            os.path.abspath(str(excel_file))
        )

        excel_app = None
        open_workbook = None

        # =========================================================
        # 1. Try to connect to an already running Excel
        # =========================================================

        try:
            excel_app = win32com.client.GetActiveObject(
                "Excel.Application"
            )
        except Exception:
            excel_app = None

        # =========================================================
        # 2. If Excel is already running, find our workbook
        # =========================================================

        if excel_app is not None:

            for workbook in excel_app.Workbooks:

                try:
                    workbook_path = os.path.normcase(
                        os.path.abspath(
                            str(workbook.FullName)
                        )
                    )

                    if workbook_path == target_path:
                        open_workbook = workbook
                        break

                except Exception:
                    continue

        # =========================================================
        # 3. If workbook is NOT open, open it automatically
        # =========================================================

        if open_workbook is None:

            # If Excel itself is not running, start it.
            if excel_app is None:

                excel_app = win32com.client.Dispatch(
                    "Excel.Application"
                )

            # Make Excel visible to the technician.
            excel_app.Visible = True

            # Open the existing master workbook.
            open_workbook = excel_app.Workbooks.Open(
                target_path
            )

        # =========================================================
        # 4. Find Calibration Register worksheet
        # =========================================================

        worksheet = None

        for sheet in open_workbook.Worksheets:

            try:
                if sheet.Name == "Calibration Register":
                    worksheet = sheet
                    break
            except Exception:
                continue

        # =========================================================
        # 5. Create worksheet if it doesn't exist
        # =========================================================

        if worksheet is None:

            worksheet = open_workbook.Worksheets.Add(
                After=open_workbook.Worksheets(
                    open_workbook.Worksheets.Count
                )
            )

            worksheet.Name = "Calibration Register"

            # -----------------------------------------------------
            # Headers
            # -----------------------------------------------------

            for column, header in enumerate(
                EXCEL_HEADERS,
                start=1
            ):

                cell = worksheet.Cells(
                    1,
                    column
                )

                cell.Value = header
                cell.Font.Bold = True
                cell.Font.Name = "Arial"
                cell.HorizontalAlignment = -4108  # xlCenter
                cell.VerticalAlignment = -4108    # xlCenter

            worksheet.Application.ActiveWindow.SplitRow = 1
            worksheet.Application.ActiveWindow.FreezePanes = True

        # =========================================================
        # 6. Make sure headers exist
        # =========================================================

        for column, header in enumerate(
            EXCEL_HEADERS,
            start=1
        ):

            current_header = worksheet.Cells(
                1,
                column
            ).Value

            if current_header is None:
                worksheet.Cells(
                    1,
                    column
                ).Value = header

        # =========================================================
        # 7. Find "Cert. No" column
        # =========================================================

        certificate_column = (
            EXCEL_HEADERS.index("Cert. No") + 1
        )

        # =========================================================
        # 8. Find the last used row
        # =========================================================

        last_row = worksheet.Cells(
            worksheet.Rows.Count,
            certificate_column
        ).End(-4162).Row  # xlUp

        if last_row < 1:
            last_row = 1

        # =========================================================
        # 9. Find existing certificate
        # =========================================================

        existing_row = None

        for row in range(
            2,
            last_row + 1
        ):

            cell_value = worksheet.Cells(
                row,
                certificate_column
            ).Value

            if cell_value is None:
                continue

            if (
                str(cell_value).strip()
                ==
                str(certificate.number).strip()
            ):
                existing_row = row
                break

        # =========================================================
        # 10. Determine row to write
        # =========================================================

        if existing_row is None:

            row_number = max(
                last_row + 1,
                2
            )

        else:

            row_number = existing_row

        # =========================================================
        # 11. Get certificate data
        # =========================================================

        values = certificate_to_excel_row(
            certificate
        )

        ratio_column = EXCEL_HEADERS.index("Ratio") + 1

        # =========================================================
        # 12. Write certificate data
        # =========================================================

        for column, value in enumerate(
            values,
            start=1
        ):

            cell = worksheet.Cells(
                row_number,
                column
            )
            # Ratio must stay a plain number. Without this, Excel can
            # turn things like 1/4 into a date and keep the date format.
            if column == ratio_column:
                cell.NumberFormat = "General"

            if value is None:

                cell.Value = ""

            elif isinstance(value, Decimal):

                cell.Value = float(value)

            if value is None:

                cell.Value = ""

            elif isinstance(value, Decimal):

                cell.Value = float(value)

            elif isinstance(value, datetime):

                cell.Value = value

            elif isinstance(value, date):

                cell.Value = datetime(
                    value.year,
                    value.month,
                    value.day
                )

            else:

                cell.Value = value

            # Basic formatting
            cell.Font.Name = "Arial"
            cell.VerticalAlignment = -4108  # xlCenter

        # =========================================================
        # 13. Date formatting
        # =========================================================

        date_headers = {
            "Cal. Date",
            "Due Date",
            "Issue Date",
        }

        for column, header in enumerate(
            EXCEL_HEADERS,
            start=1
        ):

            if header in date_headers:

                worksheet.Cells(
                    row_number,
                    column
                ).NumberFormat = "dd/mm/yyyy"

        # =========================================================
        # 14. Add borders to the row
        # =========================================================

        for column in range(
            1,
            len(EXCEL_HEADERS) + 1
        ):

            cell = worksheet.Cells(
                row_number,
                column
            )

            cell.Borders.LineStyle = 1

        # =========================================================
        # 15. Save the SAME workbook
        # =========================================================

        open_workbook.Save()

        # =========================================================
        # 16. Make Excel visible and bring it to front
        # =========================================================

        excel_app.Visible = True

        try:
            excel_app.WindowState = -4143  # xlNormal
        except Exception:
            pass

        try:
            open_workbook.Activate()
            worksheet.Activate()
        except Exception:
            pass

        return excel_file

    finally:

        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass