from pymongo import MongoClient
from config import MONGO_URI, DB_NAME, FILE_DETAILS_COLLECTION, REQUESTED_FIELDS_COLLECTION, CREDIT_COLLECTION
from datetime import datetime, timezone
from uuid import uuid4
from bson import ObjectId
from typing import List, Dict
from services.azure_llm import AzureLLMAgent, RequestedField
import traceback
# --- Initialize MongoDB Client ---
mongo_client = MongoClient(MONGO_URI)

def get_mongo_collection(collection_name):
    """Returns a MongoDB collection handle."""
    db = mongo_client[DB_NAME]
    return db[collection_name]


def fetch_requested_fields(user_id, cluster_id):
    collection = get_mongo_collection(REQUESTED_FIELDS_COLLECTION)
    query = {"userId": ObjectId(user_id), "_id": ObjectId(cluster_id)}
    results = collection.find(query, {"requestedFields": 1, "_id": 0})
    all_fields = []
    for doc in results:
        all_fields.extend(doc.get("requestedFields", []))
    return all_fields

def fetch_extracted_text(user_id, cluster_id, file_id):
    """Fetches extracted text from MongoDB if already present."""
    collection = get_mongo_collection(FILE_DETAILS_COLLECTION)
    query = {
        "_id": ObjectId(file_id),
        "userId": ObjectId(user_id),
        "clusterId": ObjectId(cluster_id)
    }
    doc = collection.find_one(query, {"extractedField": 1, "originalS3File": 1, "pageCount": 1, "originalFile":1})
    if not doc:
        return None, None, None
        print(f"File_name : {doc.get("originalS3File")}")
        print(f"s3 : {doc.get("originalFile")}")
    return doc.get("extractedField"), doc.get("originalS3File"), doc.get("pageCount")

def mark_file_as_failed(doc_id):
    collection = get_mongo_collection(FILE_DETAILS_COLLECTION)
    collection.update_one({"_id": ObjectId(doc_id)}, {
        "$set": {
            "processingStatus": "Failed",
            "updatedAt": datetime.now(timezone.utc)
        }
    })

def update_job_status(job_id: str, status: str, summary: dict = None, message: str = None):
    """Insert or update job status."""
    collection = get_mongo_collection("job_status")
    doc = {
        "job_id": job_id,
        "status": status,
        "updatedAt": datetime.now(timezone.utc)
    }
    if summary:
        doc["summary"] = summary
    if message:
        doc["message"] = message

    collection.update_one({"job_id": job_id}, {"$set": doc}, upsert=True)


def fetch_job_status(job_id: str):
    """Retrieve job status by job_id."""
    collection = get_mongo_collection("job_status")
    return collection.find_one({"job_id": job_id}, {"_id": 0})  # Exclude internal _id


def extract_fields_with_llm(full_text: str, requested_fields_raw: List[Dict], agent: AzureLLMAgent) -> List[str]:
    extracted_values = []
    for field_dict in requested_fields_raw:
        try:
            field = RequestedField(**field_dict)
        except Exception as e:
            print(f"❌ Invalid field: {field_dict} -> {e}")
            extracted_values.append("NA")
            continue

        prompt = f"""
        You are an AI assistant that extracts values from OCR text.

        OCR Text:
        {full_text}

        Please extract:
        - Field Name: {field.field_name}
        - Data Type: {field.field_datatype}
        - Description: {field.field_desc}

        IMPORTANT STRICT RULES:

        1. Extract ONLY the exact value that explicitly exists in the OCR text.
        2. Do NOT guess.
        3. Do NOT infer from MRZ lines.
        4. Do NOT reconstruct missing values.
        5. Do NOT split full names.
        6. If the field label exists but the value is empty → return null.
        7. If the field does not exist in the OCR text → return null.
        8. Never return words that are not explicitly present in the OCR text.
        9. Never duplicate another field’s value.

        Return ONLY the raw value.
        If no explicit value exists → return null.
        No explanation.
        No extra text.
        """
        value = agent.complete(prompt)
        extracted_values.append(value.strip())
    return extracted_values


def update_extracted_values_to_mongo(user_id, cluster_id, doc_id, fields, extracted_field_list, full_text):
    collection = get_mongo_collection(FILE_DETAILS_COLLECTION)
    filter_query = {"_id": ObjectId(doc_id)}
    updated = {}

    for idx, field in enumerate(fields):
        key = field["fieldName"]
        value = extracted_field_list[idx] if idx < len(extracted_field_list) else "--"
        updated[key] = [value or "--"]
        
    max_len = max(len(v) for v in updated.values())
    for k, v in updated.items():
        while len(v) < max_len:
            v.append("NA")
    print(f"Test Data : {updated}")
    update_query = {
        "$set": {
            "extractedValues": updated,
            "updatedExtractedValues": updated,
            "processingStatus": "Completed",   
            "extratedText": full_text,
            "updatedAt": datetime.now(timezone.utc)
        }
    }

    result = collection.update_one(filter_query, update_query, upsert=True)
    return {"status": "success" if result.modified_count > 0 else "no-change"}

def update_debit_credit(credit_id):
    collection = get_mongo_collection(CREDIT_COLLECTION)
    print('credit_collection',CREDIT_COLLECTION)

    if not credit_id:
        return {"status": "error", "message": "creditId is required"}

    result = collection.update_one(
        {"_id": ObjectId(credit_id)},  # match document
        {
            "$set": {
                "type": "debited",
                "updatedAt": datetime.now(timezone.utc)
            }
        }
    )
    
    print('updated credits')

    if result.matched_count == 0:
        return {"status": "error", "message": "No credit record found"}

    return {
        "status": "success",
        "message": "✅ Credit updated successfully",
        "modified_count": result.modified_count
    }
    
    


def delete_credit_record(credit_id, file_id=None):
    """
    Deletes a credit record by creditId.
    Also updates file details status to 'Failed' if file_id is provided.
    Raises errors when creditId missing or not found.
    """
    collection_credits = get_mongo_collection(CREDIT_COLLECTION)
    collection_filedetails=get_mongo_collection(FILE_DETAILS_COLLECTION)
    if not credit_id:
        raise ValueError("creditId is required but missing")

    try:
        # ---------- OLD LOGIC (unchanged) ----------
        result = collection_credits.update_one(
            {"_id": ObjectId(credit_id)},
            {
                "$set": {
                    "status": "0",
                    "updatedAt": datetime.utcnow().isoformat() + "Z"
                }
            }
        )

        if result.matched_count == 0:
            raise LookupError(f"No credit record found for creditId={credit_id}")

        print(f"🗑️ Soft-deleted credit record {credit_id} (status set to 0)")

        # ---------- NEW FILE-DETAILS UPDATE ----------
        if file_id:
            file_update_result = collection_filedetails.update_one(
                {"_id": ObjectId(file_id)},
                {
                    "$set": {
                        "processingStatus": "Failed",
                        "updatedAt": datetime.utcnow().isoformat() + "Z"
                    }
                }
            )

            print(f"⚠️ Marked FAILED for file {file_id}, modified: {file_update_result.modified_count}")
        else:
            print("⚠️ file_id not provided, skipping file update.")

        return {
            "status": "success",
            "creditId": credit_id,
            "fileId": file_id
        }

    except Exception as e:
        print(f"❌ Error deleting creditId={credit_id}: {e}")
        traceback.print_exc()
        return {"status": 'error', "message": str(e)}
