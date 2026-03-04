
# 📄 YellowChunks Document Processing API

A Python Flask-based API to extract structured data from documents using:

* **AWS Textract** for OCR
* **Azure OpenAI** (via GPT) for intelligent field extraction
* **MongoDB** for data storage
* **S3** for file input storage



## 🚀 Features

* OCR using **Textract asynchronous** jobs with bounding box data
* Dynamic **field extraction** using LLM prompt engineering
* **Credit tracking** per user/cluster
* Field validation using **Pydantic**
* Robust error handling and MongoDB updates



## 📁 Folder Structure

```bash
.
├── app.py                 # Main Flask API file
├── .env                  # Environment variables 
├── requirements.txt      # Python dependencies
└── README.md             # You're here
```



## 🔧 Environment Setup

Create a `.env` file in the root directory:

```env
MONGO_URI=mongodb+srv://<username>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
DB_NAME=assessment-portal
FILE_DETAILS=tb_file_details
REQUESTED_FIELDS=tb_clusters
CREDIT=tb_credits

AWS_REGION=ap-south-1

AZURE_OPENAI_API_KEY=your_azure_openai_api_key
AZURE_OPENAI_ENDPOINT=https://your-resource-name.openai.azure.com/
AZURE_OPENAI_API_VERSION=2025-01-01-preview
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
```


## 📦 Installation

```bash
# Clone the repository
git clone https://github.com/your-org/yellowchunks-api.git
cd yellowchunks-api

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```



## ▶️ Running the API

```bash
# Make sure .env is configured
python app.py
# API will run on http://0.0.0.0:5000
```



## 🔌 API Endpoint

### `POST /run-processing`

Processes all documents in a user's cluster.

**Request Body:**

```json
{
  "userId": "USER_OBJECT_ID",
  "clusterId": "CLUSTER_OBJECT_ID",
  "bucket": "yellow-checks-test"  // optional, defaults to this
}
```

**Response:**

```json
{
  "status": "success",
  "summary": [
    {
      "file": "invoice_123.pdf",
      "pageCount": 3,
      "fields": {
        "name": "Ragu",
        "mail": "ragu@mail.com"
      }
    }
  ]
}
```


## 📚 MongoDB Collections

* `tb_clusters`: Holds requested fields for a given cluster
* `tb_file_details`: Stores extracted values, bounding boxes, processing status
* `tb_credits`: Tracks user billing by deducting credits per processed page



## 🧠 Tech Stack

| Feature          | Service          |
| ---------------- | ---------------- |
| OCR              | AWS Textract     |
| Field Extraction | Azure OpenAI GPT |
| Database         | MongoDB Atlas    |
| API Framework    | Flask            |



## 🧪 Sample Output

```json
{
  "boundingValues": {
    "invoice1.pdf": [
      {"text": "Name: Ragu", "bbox": {...}, "pageNo": 1},
      ...
    ]
  },
  "extractedValues": {
    "name": ["Ragu"],
    "email": ["ragu@example.com"]
  }
}
```



## 🧾 Billing Logic

* Each processed **page** deducts **1 credit**
* Credit logs are inserted into `tb_credits` with metadata



## 🛡️ Notes

* Ensure AWS credentials are properly configured in your Lambda environment (IAM role)
* The Azure deployment name must match the one set up in your Azure OpenAI resource

