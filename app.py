# Importing required libraries and modules
from flask import Flask, jsonify, request, send_file
from pymongo import MongoClient
from fpdf import FPDF
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import logging
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# MongoDB connection
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client.mydatabase
metrics_collection = db.system_metrics  # Collection for system metrics
daily_max_collection = db.daily_max_metrics  # Collection for daily maximum metrics


# Route to add data
@app.route("/add", methods=["POST"])
def add_data():
    data = request.json  # Retrieve JSON data from the request
    if not data:
        return jsonify({"error": "Invalid data provided"}), 400  # Handle invalid data

    metrics_collection.insert_one(data)  # Insert data into MongoDB

    # Check if there are 3 shifts for a given date, then calculate max metrics
    date = data.get("date")
    shift_count = metrics_collection.count_documents({"date": date})
    if shift_count == 3:
        calculate_and_store_max_metrics(date)

    return jsonify({"message": "Data inserted successfully!"}), 201

# Set logging level to DEBUG
logging.basicConfig(level=logging.DEBUG)

# Function to calculate and store daily maximum metrics
def calculate_and_store_max_metrics(date):
    shifts = list(metrics_collection.find({"date": date}))  # Get all shifts for the date
    logging.debug(f"Shifts for {date}: {shifts}")

    # Check if there are 3 shifts, else log an error
    if len(shifts) < 3:
        logging.error(f"Not enough shifts for {date}. Expected 3, found {len(shifts)}.")
        return

    # Initialize dictionaries to store max values
    max_cpu = {}
    max_memory = {}
    application_availability = {}

    # Loop through each shift and calculate max values
    for shift in shifts:
        cpu_usage = shift.get("cpu_usage", {})
        memory_usage = shift.get("memory_usage", {})
        app_avail = shift.get("Application_Availability", {})

        # Update max CPU usage
        for key, value in cpu_usage.items():
            if isinstance(value, (int, float)):  # Ensure the value is numeric
                if key not in max_cpu or value > max_cpu[key]:
                    max_cpu[key] = value

        # Update max memory usage
        for key, value in memory_usage.items():
            if isinstance(value, (int, float)):  # Ensure the value is numeric
                if key not in max_memory or value > max_memory[key]:
                    max_memory[key] = value

        # Update application availability
        for key, value in app_avail.items():
            if key not in application_availability:
                application_availability[key] = value

    # Prepare the max metrics data
    max_metrics = {
        "date": date,
        "cpu_usage": max_cpu,
        "memory_usage": max_memory,
        "application_availability": application_availability
    }

    # Insert max metrics into MongoDB
    daily_max_collection.insert_one(max_metrics)
    logging.debug(f"Inserted max metrics: {max_metrics}")

# Route to retrieve all metrics data
@app.route("/get", methods=["GET"])
def get_all_data():
    data = list(metrics_collection.find({}, {"_id": 0}))  # Get all data without _id
    return jsonify(data)

# Route to retrieve metrics data by date and shift
@app.route("/get/<string:date>/<int:day_shift>", methods=["GET"])
def get_data_by_shift(date, day_shift):
    data = metrics_collection.find_one({"date": date, "day-shift": day_shift}, {"_id": 0})
    if not data:
        return jsonify({"error": "No data found for the given date and shift"}), 404
    return jsonify(data)

# Route to get the maximum metrics data for a given date
@app.route("/get-daily-max/<string:date>", methods=["GET"])
def get_daily_max(date):
    max_metrics = daily_max_collection.find_one({"date": date}, {"_id": 0})
    if not max_metrics:
        return jsonify({"error": f"No max metrics found for the given date: {date}"}), 404
    return jsonify(max_metrics)



# Route to export data for a specific shift into a PDF
@app.route("/export-native-shift-pdf/<string:date>/<int:shift>", methods=["GET"])
def export_by_shift_pdf(date, shift):
    data = metrics_collection.find_one({"date": date, "day-shift": shift}, {"_id": 0})
    if not data:
        return jsonify({"error": f"No data found for the given date: {date} and shift: {shift}"}), 404
    
    print(data)
    file_name = f"{date}_shift_{shift}_report.pdf"
    create_daily_max_pdf(date, data, "./avaxia-logo.png", output_file=file_name)  # Generate the PDF

    return send_file(file_name, as_attachment=True)  # Send the PDF as an attachment

# Route to export the daily maximum metrics data into a PDF
@app.route("/export-pdf/<string:date>", methods=["GET"])
def export_daily_max_pdf(date):
    data = daily_max_collection.find_one({"date": date}, {"_id": 0})
    if not data:
        return jsonify({"error": "No data found for the given date"}), 404

    file_name = f"{date}_daily_max_report.pdf"
    create_daily_max_pdf(date, data, "./avaxia-logo.png", output_file=file_name)

    return send_file(file_name, as_attachment=True)

# Route to send an email with the report attached
@app.route("/send-email/<string:date>", methods=["POST"])
def send_email(date):
    recipient_email = request.json.get("recipient_email")
    if not recipient_email:
        return jsonify({"error": "Recipient email is required"}), 400

    pdf_path = f"{date}_metrics.pdf"
    export_pdf(date)  # Export the PDF report

    EMAIL_USER = os.getenv("EMAIL_USER")
    EMAIL_PASS = os.getenv("EMAIL_PASS")
    subject = f"Daily Metrics for {date}"

    # Create email message with attachment
    message = MIMEMultipart()
    message["From"] = EMAIL_USER
    message["To"] = recipient_email
    message["Subject"] = subject

    body = "Hello team, please find the attached report of the PTO project. Best regards."
    message.attach(MIMEText(body, "plain"))

    with open(pdf_path, "rb") as attachment:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(attachment.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f"attachment; filename= {pdf_path}",
        )
        message.attach(part)

    # Send the email via SMTP
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, recipient_email, message.as_string())

    return jsonify({"message": "Email sent successfully!"})

# Route to update shift data
@app.route("/update/<string:date>/<int:day_shift>", methods=["PUT"])
def update_shift_data(date, day_shift):
    updated_data = request.json
    if not updated_data:
        return jsonify({"error": "Invalid data provided"}), 400
    result = metrics_collection.update_one({"date": date, "day-shift": day_shift}, {"$set": updated_data})
    if result.matched_count == 0:
        return jsonify({"error": "No data found to update for the given date and shift"}), 404
    return jsonify({"message": "Data updated successfully!"})

# Route to delete shift data
@app.route("/delete/<string:date>/<int:day_shift>", methods=["DELETE"])
def delete_shift_data(date, day_shift):
    result = metrics_collection.delete_one({"date": date, "day-shift": day_shift})
    if result.deleted_count == 0:
        return jsonify({"error": "No data found to delete for the given date and shift"}), 404
    return jsonify({"message": "Data deleted successfully!"})

# Function to create daily max PDF report
def create_daily_max_pdf(date, max_data, header_image_path, output_file="daily_max_report.pdf"):
    class PDF(FPDF):
        def header(self):
            self.image(header_image_path, x=10, y=-12, w=190)  # Add image to header
            self.ln(20)  # Move down after image

        def footer(self):
            self.set_y(-15)
            self.set_font("Arial", "I", 8)
            self.cell(0, 10, f"Page {self.page_no()}", 0, 0, "C")  # Page number in footer

    pdf = PDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    # Add title and date
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, f"Date: {date}", ln=True, align="C")
    pdf.ln(10)

    # Add application metrics table
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "Organizational Applications: PTO", ln=True)
    pdf.ln(5)
    pdf.set_font("Arial", size=12)

    pdf.cell(60, 10, "Component", 1, 0, "C")
    pdf.cell(40, 10, "CPU Usage (core)", 1, 0, "C")
    pdf.cell(50, 10, "Memory Usage (MiB)", 1, 0, "C")
    pdf.cell(45, 10, "Availability (%)", 1, 1, "C")

    # Add data for each component
    for component in ["blc-be", "blc-fe", "gco-be", "gco-fe", "sbp-fe"]:
        cpu = max_data["cpu_usage"].get(component, "Not Available")
        memory = max_data["memory_usage"].get(component, "Not Available")
        availability = "100%" if component != "sbp-be" else "Down"
        
        pdf.cell(60, 10, component, 1, 0, "C")
        pdf.cell(40, 10, str(cpu), 1, 0, "C")
        pdf.cell(50, 10, f"{memory} MiB" if memory != "Not Available" else memory, 1, 0, "C")
        pdf.cell(45, 10, availability, 1, 1, "C")

    pdf.ln(10)

    # Add tools metrics table
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "Tools:", ln=True)
    pdf.ln(5)
    pdf.set_font("Arial", size=12)

    pdf.cell(65, 10, "Component", 1, 0, "C")
    pdf.cell(40, 10, "CPU Usage (core)", 1, 0, "C")
    pdf.cell(50, 10, "Memory Usage (MiB)", 1, 0, "C")
    pdf.cell(40, 10, "Availability (%)", 1, 1, "C")

    # Add data for tools components
    for component in max_data["cpu_usage"]:
        if component not in ["blc-be", "blc-fe", "gco-be", "gco-fe", "sbp-fe"]:
            cpu = max_data["cpu_usage"].get(component, "Not Available")
            memory = max_data["memory_usage"].get(component, "Not Available")
            availability = "100%"  

            pdf.cell(65, 10, component, 1, 0, "C")
            pdf.cell(40, 10, str(cpu), 1, 0, "C")
            pdf.cell(50, 10, f"{memory} MiB" if memory != "Not Available" else memory, 1, 0, "C")
            pdf.cell(40, 10, availability, 1, 1, "C")

    pdf.output(output_file)  # Output PDF
    return output_file
#

# Run the app in debug mode

if __name__ == "__main__":
    HOST = os.getenv("HOST")
    PORT = os.getenv("PORT")
    app.run(host=HOST,port=PORT)
