import time
import boto3
import random
import os 
from dotenv import load_dotenv
import base64
from urllib.parse import urlparse
load_dotenv()
from anthropic import Anthropic
s3 = boto3.client("s3")

client_claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


TEXTRACT_REGIONS = [
     "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-central-1",
    "ap-southeast-1"
]

TEMP_BUCKET_PREFIX = "yellow-temp-"
S3_SOURCE_REGION = "ap-south-1"

def get_random_textract_client():
    region = random.choice(TEXTRACT_REGIONS)
    print(f"🌍 Using Textract in region: {region}")
    textract = boto3.client("textract", region_name=region)
    temp_bucket = f"{TEMP_BUCKET_PREFIX}{region}"
    return textract, region, temp_bucket

def ensure_temp_bucket_exists(temp_bucket, region):
    s3 = boto3.client('s3', region_name=region)
    try:
        s3.head_bucket(Bucket=temp_bucket)
        print(f"✅ Temp bucket already exists: {temp_bucket}")
    except s3.exceptions.ClientError:
        print(f"🛠️ Temp bucket used")


def copy_to_temp_bucket(source_bucket, source_key, temp_bucket, region):
    s3_source = boto3.client('s3', region_name=S3_SOURCE_REGION)
    s3_dest = boto3.client('s3', region_name=region)

    ensure_temp_bucket_exists(temp_bucket, region)

    temp_key = source_key.split("/")[-1]
    copy_source = {'Bucket': source_bucket, 'Key': source_key}
    print(f"📤 Copying file to temporary bucket: s3://{temp_bucket}/{temp_key}")
    s3_dest.copy(copy_source, temp_bucket, temp_key)
    return temp_key

def cleanup_temp_bucket(temp_bucket, temp_key, region):
    s3 = boto3.client('s3', region_name=region)
    try:
        print(f"🗑️ Deleting temporary file: s3://{temp_bucket}/{temp_key}")
        s3.delete_object(Bucket=temp_bucket, Key=temp_key)
        #print(f"🗑️ Deleting temporary bucket: s3://{temp_bucket}")
        #s3.delete_bucket(Bucket=temp_bucket)
    except Exception as e:
        print(f"⚠️ Error deleting temp file or bucket: {e}")
        
def run_claude(original_file_url, max_wait_sec=120):
    """
    Download directly from originalFile URL
    Send to Claude OCR
    Return: full_text, filename, page_count, None
    """

    try:
        parsed_url = urlparse(original_file_url)

        bucket = parsed_url.netloc.split(".")[0]
        key = parsed_url.path.lstrip("/")

        filename = key.split("/")[-1]
        local_path = f"/tmp/{filename}"

        print(f"📥 Downloading file from: s3://{bucket}/{key}")

        s3 = boto3.client("s3", region_name="ap-south-1")
        s3.download_file(bucket, key, local_path)

        # Read file
        with open(local_path, "rb") as f:
            file_bytes = f.read()

        encoded_file = base64.b64encode(file_bytes).decode("utf-8")

        # Detect media type
        if filename.lower().endswith(".pdf"):
            media_type = "application/pdf"
            doc_type = "document"
        elif filename.lower().endswith(".png"):
            media_type = "image/png"
            doc_type = "image"
        elif filename.lower().endswith(".jpg") or filename.lower().endswith(".jpeg"):
            media_type = "image/jpeg"
            doc_type = "image"
        else:
            raise ValueError("Unsupported file type")

        print("🤖 Sending document to Claude OCR...")

        response = client_claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": doc_type,
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": encoded_file,
                            },
                        },
                        {
                            "type": "text",
                            "text": """
                                    You are extracting data from a passport image.

                                    EXTRACT SURNAME AND GIVEN NAME FROM MRZ ONLY - THIS IS THE MOST ACCURATE METHOD:

                                    MRZ LINE 1 STRUCTURE: P<{Country Code}<{SURNAME}<<{GIVEN NAME(S)}<<<...
                                    - After "P<IND<" (or P<{any 3 letter country}<) → everything before the FIRST "<<" is the SURNAME
                                    - After the FIRST "<<" → everything before the next "<<" or end of filler "<" is the GIVEN NAME
                                    - If there is NO text between "P<IND<" and "<<" → Surname is BLANK
                                    - "<" within a name segment means a SPACE in the actual name

                                    MRZ LINE 2 STRUCTURE: {Passport No}{Check digit}{Country}{Date of Birth}{Check}{Sex}{Expiry}{Check}...
                                    - First 9 characters = Passport Number
                                    - Characters 10-15 (after country code) = Date of Birth (YYMMDD)

                                    EXAMPLES:
                                    Line 1: P<IND<<NARENDRA<KUMAR<<<<<<<<<<<<<<<<<<<<<
                                    → Between "P<IND<" and first "<<" = NOTHING → Surname = (blank)
                                    → After first "<<" = "NARENDRA<KUMAR" → Given Name = NARENDRA KUMAR

                                    Line 1: P<IND<SURESH<<KUMAR<<<<<<<<<<<<<<<<<<<<<<<
                                    → Between "P<IND<" and first "<<" = "SURESH" → Surname = SURESH
                                    → After first "<<" = "KUMAR" → Given Name = KUMAR

                                    Line 1: P<IND<SHARMA<<RAHUL<KUMAR<<<<<<<<<<<<<<<<
                                    → Between "P<IND<" and first "<<" = "SHARMA" → Surname = SHARMA  
                                    → After first "<<" = "RAHUL<KUMAR" → Given Name = RAHUL KUMAR

                                    Now extract all fields:

                                    **Surname:** (parse from MRZ Line 1 using rules above)
                                    **Given Name:** (parse from MRZ Line 1 using rules above)
                                    **Passport No:** (first 9 chars of MRZ Line 2)
                                    **Nationality:**
                                    **Date of Birth:**
                                    **Sex:**
                                    **Place of Birth:**
                                    **Place of Issue:**
                                    **Date of Issue:**
                                    **Date of Expiry:**
                                    **MRZ:**
                                    (line 1)
                                    (line 2)

                                    Return only the extracted fields above. No extra text, warnings, or disclaimers.
                                    """
                        },
                    ],
                }
            ],
        )

        full_text = response.content[0].text.strip()

        page_count = full_text.count("\f") + 1 if full_text else 1

        print("✅ Claude OCR completed")

        return full_text, filename, page_count, None

    except Exception as e:
        print(f"❌ Claude OCR Error: {e}")
        return "", "", 0, None