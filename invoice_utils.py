import pyodbc
import json
import re
from datetime import datetime, timedelta
import streamlit as st

def extract_json_block(text):
    """Extract the first JSON object found in a text block."""
    try:
        # Find the first {...} block in the text
        json_str = re.search(r'\{.*\}', text, re.DOTALL).group(0)
        return json.loads(json_str)
    except Exception as e:
        raise ValueError(f"Failed to extract valid JSON: {e}")
    
def sql_connection():
    """Establish a connection to the SQL Server database."""
    server = 'DESKTOP-62M44AT'
    database = 'LLM'
    driver = '{ODBC Driver 17 for SQL Server}'  # Adjust if using a different version

    conn = pyodbc.connect(
        f'DRIVER={driver};SERVER={server};DATABASE={database};Trusted_Connection=yes;'
    )
    return conn


def get_invoice_count():
    """Get the total count of invoices in the database."""
    conn = sql_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM Invoices")
    count = cursor.fetchone()[0]
    
    cursor.close()
    conn.close()
    
    return count

def get_vendor_count():
    """Get the total count of vendors in the database."""
    conn = sql_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM Vendors")
    count = cursor.fetchone()[0]
    
    cursor.close()
    conn.close()
    
    return count

def get_po_count():
    """Get the total count of purchase orders in the database."""
    conn = sql_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM PurchaseOrders")
    count = cursor.fetchone()[0]
    
    cursor.close()
    conn.close()
    
    return count

def insert_invoice_to_sql(data):
    conn = sql_connection()
    cursor = conn.cursor()

    waybill_number = data.get("Air Way bill Number")
    
    # Step 1: Check for existing invoice
    cursor.execute("SELECT COUNT(*) FROM Invoices WHERE WaybillNumber = ?", (waybill_number,))
    count = cursor.fetchone()[0]

    if count > 0:
        st.warning(f"⚠️ Invoice with Waybill Number `{waybill_number}` already exists. Skipping insert.")
        cursor.close()
        conn.close()
        return False

    print(data)

    # Step 2: Insert new invoice
    for item in data.get("Invoice Line Items", []):
        cursor.execute(f"""
            INSERT INTO Invoices (
                WaybillNumber, DateOfExportation, BillToName, BillToAddress,
                ShipToName, ShipToAddress, ItemDescription, Quantity, UnitPrice, Total
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            waybill_number,
            data.get("Date of Exportation"),
            data.get("Bill To Name"),
            data.get("Bill To Address"),
            data.get("Ship To Name"),
            data.get("Ship To Address"),
            item.get("Description").replace("'", "''") if item.get("Description") else '',
            item.get("Quantity"),
            item.get("Unit Price"),
            item.get("Total")
        ))

        # Display inserted line item
        st.success(f"✅ Inserted Line: {item.get('Description')} | Qty: {item.get('Quantity')} | Unit Price: {item.get('Unit Price')} | Total: {item.get('Total')}")

        # Add reminders and PO updates
        body = insert_payment_reminder(
            cursor=cursor,
            bill_to_name=data.get("Bill To Name"),
            invoice_date_str=data.get("Date of Exportation"),
            amount=item.get("Total"),
            waybill=waybill_number
        )

        check_and_update_po_delivery(
            cursor=cursor,
            vendor_name=data.get("Bill To Name"),
            item_desc=item.get("Description"),
            quantity=item.get("Quantity"),
            waybill=waybill_number,
            delivery_date_str=data.get("Date of Exportation")
        )

    conn.commit()
    cursor.close()
    conn.close()

    return body, True



def insert_payment_reminder(cursor, bill_to_name, invoice_date_str, amount, waybill):
    # Parse invoice date
    invoice_date = datetime.strptime(invoice_date_str, "%d/%m/%Y")  # adjust format as needed

    # Get VendorID and CreditPeriodDays
    cursor.execute("""
        SELECT VendorID, CreditPeriodDays FROM Vendors
        WHERE VendorName = ?
    """, (bill_to_name,))
    row = cursor.fetchone()

    if not row:
        raise ValueError(f"No vendor found with name: {bill_to_name}")

    vendor_id, credit_days = row
    due_date = invoice_date + timedelta(days=credit_days)

    # Insert reminder
    cursor.execute(f"""
        INSERT INTO PaymentReminders (VendorID, InvoiceDate, DueDate, WaybillNumber, Amount)
        VALUES ('{vendor_id}', '{invoice_date.date()}', '{due_date.date()}', '{waybill}', '{amount}')
    """)

    # Display the payment reminder details details in streamlit UI
    st.session_state["payment_reminder"] = {
        "VendorID": vendor_id,
        "InvoiceDate": invoice_date.date(),
        "DueDate": due_date.date(),
        "WaybillNumber": waybill,
        "Amount": amount
    }
    print(f"Payment reminder created for vendor {bill_to_name} with due date {due_date.date()} and amount {amount}.")
    st.success(f"✅ Payment Reminder Created for {bill_to_name} with due date {due_date.date()} and amount {amount}.")
    return f"✅ Payment Reminder for {bill_to_name} with due date {due_date.date()} and amount {amount}."


def check_and_update_po_delivery(cursor, vendor_name, item_desc, quantity, waybill, delivery_date_str):
    # Convert to datetime
    delivery_date = datetime.strptime(delivery_date_str, "%d/%m/%Y")

    # Get VendorID
    cursor.execute("""
        SELECT VendorID FROM Vendors WHERE VendorName = ?
    """, (vendor_name,))
    row = cursor.fetchone()
    print(row)
    if not row:
        print(f"No Vendor found for delivery: {vendor_name}")
        return
    vendor_id = row[0]

    print(vendor_id)
    print(item_desc)
    
    # Check for existing PO
    cursor.execute("""
        SELECT POID, OrderedQuantity, ReceivedQuantity FROM PurchaseOrders
        WHERE VendorID = ? AND ItemDescription = ?
    """, (vendor_id, item_desc))
    po_row = cursor.fetchone()

    if not po_row:
        print(f"No PO found for item: {item_desc} from vendor: {vendor_name}")
        return

    poid, ordered_qty, received_qty = po_row
    new_received_qty = received_qty + int(quantity)

    # Check if already delievred or not and then Insert delivery record
    cursor.execute("""
        SELECT COUNT(*) FROM DeliveryReceipts
        WHERE POID = ? AND WaybillNumber = ?
    """, (poid, waybill))
    delivery_count = cursor.fetchone()[0]
    if delivery_count > 0:
        print(f"Delivery for PO {poid} with Waybill {waybill} already exists. Skipping insert.")
        return
    # Insert delivery record
    print(f"Inserting delivery record for PO {poid} with Waybill {waybill} and quantity {quantity}.")
    
    cursor.execute("""
        INSERT INTO DeliveryReceipts (POID, WaybillNumber, DeliveredQuantity, DeliveryDate)
        VALUES (?, ?, ?, ?)
    """, (poid, waybill, quantity, delivery_date.date()))

    # Update PO received quantity
    cursor.execute("""
        UPDATE PurchaseOrders SET ReceivedQuantity = ?
        WHERE POID = ?
    """, (new_received_qty, poid))

    if new_received_qty >= ordered_qty:
        print(f"✅ PO {poid} fulfilled with delivery of {new_received_qty} items.")
        st.success(f"✅ PO {poid} fulfilled with delivery of {new_received_qty} items.")
    else:
        print(f"🕒 PO {poid} partially fulfilled ({new_received_qty}/{ordered_qty}).")
        st.success(f"🕒 PO {poid} partially fulfilled ({new_received_qty}/{ordered_qty}).")



