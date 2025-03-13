import os
import time
from dotenv import load_dotenv
from pathlib import Path
from tqdm import tqdm
from datetime import datetime, timezone
import pandas as pd

env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents import SearchClient
from azure.search.documents.indexes.models import (
    SearchIndex, SimpleField, SearchableField, SearchField, 
    SearchFieldDataType, VectorSearch, HnswAlgorithmConfiguration,
    VectorSearchAlgorithmKind, VectorSearchProfile, SemanticConfiguration,
    SemanticPrioritizedFields, SemanticField, SemanticSearch
)

from openai import AzureOpenAI

def create_cms1500_healthcare_index(index_client, index_name, dimensions=3072):
    print(f"🔧 Creating/updating CMS-1500 healthcare index '{index_name}'...")

    fields = [
        # Core fields
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        
        # Financial fields - crucial for aggregation
        SimpleField(name="claimAmount", type=SearchFieldDataType.Double, filterable=True, sortable=True, facetable=True),
        SimpleField(name="lineItemCharge", type=SearchFieldDataType.Double, filterable=True, sortable=True, facetable=True),
        SimpleField(name="amountPaid", type=SearchFieldDataType.Double, filterable=True, sortable=True, facetable=True),
        SimpleField(name="balanceDue", type=SearchFieldDataType.Double, filterable=True, sortable=True, facetable=True),
        
        # Date fields
        SimpleField(name="serviceStartDate", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        SimpleField(name="serviceEndDate", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        
        # Patient information
        SearchableField(name="patientName", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="patientAddress", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="patientDOB", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        SimpleField(name="patientSex", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="patientState", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="patientCity", type=SearchFieldDataType.String, filterable=True, facetable=True),
        
        # Provider information 
        SearchableField(name="providerName", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="providerNPI", type=SearchFieldDataType.String, filterable=True),
        
        # Insurance information
        SearchableField(name="insuranceCompany", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="insurancePlan", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="insuredID", type=SearchFieldDataType.String, filterable=True),
        
        # Clinical information
        SearchableField(name="diagnosisCodes", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="procedureCode", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="placeOfService", type=SearchFieldDataType.String, filterable=True, facetable=True),
        
        # Flag for aggregation
        SimpleField(name="isAggregationCandidate", type=SearchFieldDataType.Boolean, filterable=True),
        
        # Vector field
        SearchField(
            name="vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            vector_search_dimensions=dimensions,
            vector_search_profile_name="healthcare-vector-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name="healthcare-hnsw",
                kind=VectorSearchAlgorithmKind.HNSW,
                parameters={
                    "m": 10,
                    "efConstruction": 400,
                    "efSearch": 100
                }
            )
        ],
        profiles=[
            VectorSearchProfile(
                name="healthcare-vector-profile",
                algorithm_configuration_name="healthcare-hnsw",
            )
        ],
    )

    semantic_config = SemanticConfiguration(
        name="healthcare-semantic-config",
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="patientName"),
            keywords_fields=[
                SemanticField(field_name="diagnosisCodes"),
                SemanticField(field_name="procedureCode"),
                SemanticField(field_name="providerName"),
                SemanticField(field_name="insuranceCompany")
            ],
            content_fields=[
                SemanticField(field_name="content")
            ]
        )
    )

    semantic_search = SemanticSearch(configurations=[semantic_config])

    index = SearchIndex(
        name=index_name, 
        fields=fields, 
        vector_search=vector_search, 
        semantic_search=semantic_search
    )

    result = index_client.create_or_update_index(index=index)
    print(f"✅ CMS-1500 Healthcare Index '{result.name}' created with {dimensions} dimensions")
    return result

def generate_embeddings(openai_client, text, model="text-embedding-ada-002", max_retries=3):
    """
    Generate embeddings with retry logic
    
    Args:
        openai_client: Azure OpenAI client
        text: Text to embed
        model: Model deployment name
        max_retries: Maximum number of retries
    
    Returns:
        Vector embedding
    """
    for attempt in range(max_retries):
        try:
            response = openai_client.embeddings.create(
                input=text,
                model=model
            )
            return response.data[0].embedding
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"⚠️ Embedding error: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"❌ Failed to generate embedding after {max_retries} attempts: {e}")
                return [0.0] * 3072

def prepare_cms1500_documents(excel_path):
    try:
        print(f"📊 Reading CMS-1500 healthcare claims data from: {excel_path}")
        df = pd.read_excel(excel_path)

        print(f"Found {len(df)} claims with {len(df.columns)} columns")
        documents = []
        for idx, row in df.iterrows():
            content_parts = []
            for col, value in row.items():
                if pd.notna(value):
                    content_parts.append(f"{col}: {value}")
            
            content = "\n".join(content_parts)

            doc = {
                "id": str(idx),
                "content": content,
                "isAggregationCandidate": True
            }

            if 'Charges' in row and pd.notna(row['Charges']):
                try:
                    charge_value = row['Charges']
                    if isinstance(charge_value, str):
                        charge_value = charge_value.replace('$', '').replace(',', '')
                    doc["lineItemCharge"] = float(charge_value)
                except (ValueError, TypeError):
                    pass

            if 'Total_Charge' in row and pd.notna(row['Total_Charge']):
                try:
                    total_value = row['Total_Charge']
                    if isinstance(total_value, str):
                        total_value = total_value.replace('$', '').replace(',', '')
                    doc["claimAmount"] = float(total_value)
                except (ValueError, TypeError):
                    pass

            if 'Amount_Paid' in row and pd.notna(row['Amount_Paid']):
                try:
                    paid_value = row['Amount_Paid']
                    if isinstance(paid_value, str):
                        paid_value = paid_value.replace('$', '').replace(',', '')
                    doc["amountPaid"] = float(paid_value)
                except (ValueError, TypeError):
                    pass

            if 'Balance_Due' in row and pd.notna(row['Balance_Due']):
                try:
                    balance_value = row['Balance_Due']
                    if isinstance(balance_value, str):
                        balance_value = balance_value.replace('$', '').replace(',', '')
                    doc["balanceDue"] = float(balance_value)
                except (ValueError, TypeError):
                    pass

            if 'Date_of_Service_From' in row and pd.notna(row['Date_of_Service_From']):
                try:
                    service_date = pd.to_datetime(row['Date_of_Service_From'])
                    if service_date.tzinfo is None:
                        service_date = service_date.replace(tzinfo=timezone.utc)
                    doc["serviceStartDate"] = service_date.isoformat()
                except:
                    pass

            if 'Date_of_Service_To' in row and pd.notna(row['Date_of_Service_To']):
                try:
                    end_date = pd.to_datetime(row['Date_of_Service_To'])
                    if end_date.tzinfo is None:
                        end_date = end_date.replace(tzinfo=timezone.utc)
                    doc["serviceEndDate"] = end_date.isoformat()
                except:
                    pass

            if 'Patient_State' in row and pd.notna(row['Patient_State']):
                doc["patientState"] = str(row['Patient_State'])

            if 'Patient_City' in row and pd.notna(row['Patient_City']):
                doc["patientCity"] = str(row['Patient_City'])

            patient_name_parts = []
            if 'Patient_First_Name' in row and pd.notna(row['Patient_First_Name']):
                patient_name_parts.append(str(row['Patient_First_Name']))
            if 'Patient_Middle_Initial' in row and pd.notna(row['Patient_Middle_Initial']):
                patient_name_parts.append(str(row['Patient_Middle_Initial']))
            if 'Patient_Last_Name' in row and pd.notna(row['Patient_Last_Name']):
                patient_name_parts.append(str(row['Patient_Last_Name']))
            
            if patient_name_parts:
                doc["patientName"] = " ".join(patient_name_parts)

            patient_address_parts = []
            if 'Patient_Address_1' in row and pd.notna(row['Patient_Address_1']):
                patient_address_parts.append(str(row['Patient_Address_1']))
            if 'Patient_City' in row and pd.notna(row['Patient_City']):
                patient_address_parts.append(str(row['Patient_City']))
            if 'Patient_State' in row and pd.notna(row['Patient_State']):
                patient_address_parts.append(str(row['Patient_State']))
            if 'Patient_ZIP_Code' in row and pd.notna(row['Patient_ZIP_Code']):
                patient_address_parts.append(str(row['Patient_ZIP_Code']))
            
            if patient_address_parts:
                doc["patientAddress"] = ", ".join(patient_address_parts)

            if 'Patient_Date_of_Birth' in row and pd.notna(row['Patient_Date_of_Birth']):
                try:
                    dob = pd.to_datetime(row['Patient_Date_of_Birth'])
                    if dob.tzinfo is None:
                        dob = dob.replace(tzinfo=timezone.utc)
                    doc["patientDOB"] = dob.isoformat()
                except:
                    pass

            if 'Patient_Sex' in row and pd.notna(row['Patient_Sex']):
                doc["patientSex"] = str(row['Patient_Sex'])

            if 'Billing_Provider_Name' in row and pd.notna(row['Billing_Provider_Name']):
                doc["providerName"] = str(row['Billing_Provider_Name'])

            if 'Billing_Provider_NPI' in row and pd.notna(row['Billing_Provider_NPI']):
                doc["providerNPI"] = str(row['Billing_Provider_NPI'])

            if 'Insurance_Company_Name' in row and pd.notna(row['Insurance_Company_Name']):
                doc["insuranceCompany"] = str(row['Insurance_Company_Name'])

            if 'Insurance_Plan_or_Program_Name' in row and pd.notna(row['Insurance_Plan_or_Program_Name']):
                doc["insurancePlan"] = str(row['Insurance_Plan_or_Program_Name'])

            if 'Insureds_ID_Number' in row and pd.notna(row['Insureds_ID_Number']):
                doc["insuredID"] = str(row['Insureds_ID_Number'])

            diagnosis_codes = []
            for i in range(1, 7):
                code_field = f'Diagnosis_Code_{i}'
                if code_field in row and pd.notna(row[code_field]):
                    diagnosis_codes.append(str(row[code_field]))
            
            if diagnosis_codes:
                doc["diagnosisCodes"] = ", ".join(diagnosis_codes)

            if 'Procedure_Code' in row and pd.notna(row['Procedure_Code']):
                doc["procedureCode"] = str(row['Procedure_Code'])

            if 'Place_of_Service' in row and pd.notna(row['Place_of_Service']):
                doc["placeOfService"] = str(row['Place_of_Service'])
            
            documents.append(doc)
        
        print(f"✅ Prepared {len(documents)} structured CMS-1500 healthcare claim documents for indexing")
        return documents
        
    except Exception as e:
        print(f"❌ Error processing CMS-1500 healthcare claims data: {str(e)}")
        import traceback
        traceback.print_exc()
        return []

def upload_documents_with_tracking(search_client, documents, openai_client, 
                                  batch_size=25, embedding_model="text-embedding-3-large"):
    total_docs = len(documents)
    success_count = 0

    num_batches = (total_docs + batch_size - 1) // batch_size

    batch_progress = tqdm(range(num_batches), desc="Uploading document batches", unit="batch")
    
    for batch_idx in batch_progress:
        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, total_docs)
        current_batch = documents[start_idx:end_idx]

        embedding_progress = tqdm(current_batch, desc=f"Generating embeddings for batch {batch_idx+1}/{num_batches}", 
                                leave=False, unit="doc")

        for doc in embedding_progress:
            doc["vector"] = generate_embeddings(openai_client, doc["content"], embedding_model)

            for key, value in list(doc.items()):
                if key != "vector" and isinstance(value, list):
                    print(f"Warning: Field {key} has array value. Converting to string.")
                    doc[key] = ", ".join(map(str, value))

        try:
            results = search_client.upload_documents(documents=current_batch)
            batch_success = sum(1 for r in results if r.succeeded)
            success_count += batch_success

            batch_progress.set_postfix({
                "success_rate": f"{batch_success}/{len(current_batch)}", 
                "total_indexed": success_count
            })

            if batch_success < len(current_batch):
                print(f"⚠️ {len(current_batch) - batch_success} documents failed in batch {batch_idx+1}")
                for i, result in enumerate(results):
                    if not result.succeeded:
                        print(f"  - Doc ID {current_batch[i]['id']} failed: {result.error_message}")
                
        except Exception as e:
            print(f"❌ Batch {batch_idx+1} failed: {str(e)}")
            print(f"Error details: {str(e)}")
    
    return success_count, total_docs

def index_cms1500_claims(
    excel_path, 
    endpoint, 
    key, 
    openai_client,
    index_name="healthcare_claims",
    embedding_model="text-embedding-3-large", 
    batch_size=25,
    skip_index_creation=False
):
    start_time = time.time()
    print(f"🚀 Starting CMS-1500 healthcare claims indexing process at {datetime.now().strftime('%H:%M:%S')}")

    index_client = SearchIndexClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(key)
    )

    search_client = SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(key)
    )

    if not skip_index_creation:
        create_cms1500_healthcare_index(index_client, index_name)
    else:
        print(f"🔍 Using existing index '{index_name}'")

    documents = prepare_cms1500_documents(excel_path)
    
    if not documents:
        print("❌ No CMS-1500 healthcare claim documents were found to index")
        return None
    
    print(f"🔄 Uploading {len(documents)} CMS-1500 healthcare claim documents to '{index_name}' (batch size: {batch_size})")
    success_count, total_docs = upload_documents_with_tracking(
        search_client, documents, openai_client, batch_size, embedding_model
    )

    success_rate = (success_count / total_docs) * 100 if total_docs > 0 else 0
    elapsed_time = time.time() - start_time
    mins, secs = divmod(elapsed_time, 60)
    
    print(f"\n✅ CMS-1500 healthcare claims indexing complete!")
    print(f"📊 Statistics:")
    print(f"   - Documents: {success_count}/{total_docs} ({success_rate:.1f}%)")
    print(f"   - Index: {index_name}")
    print(f"   - Time: {int(mins)} minutes, {int(secs)} seconds")
    
    print("\n🔍 Testing data retrieval...")
    try:
        test_results = search_client.search(
            search_text="*",
            select="id,claimAmount",
            top=50,
            filter="isAggregationCandidate eq true"
        )

        test_docs = list(test_results)
        print(f"   - Retrieved {len(test_docs)} documents for aggregation test")

        amounts = [doc['claimAmount'] for doc in test_docs if 'claimAmount' in doc and doc['claimAmount'] is not None]
        if amounts:
            avg_amount = sum(amounts) / len(amounts)
            print(f"   - Sample calculation: Average claim amount = ${avg_amount:.2f} based on {len(amounts)} claims")
        
    except Exception as e:
        print(f"⚠️ Test retrieval warning: {str(e)}")
    
    return index_name

if __name__ == "__main__":
    AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_SERVICE_ENDPOINT")
    AZURE_SEARCH_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY")
    AZURE_OPENAI_ENDPOINT = os.getenv("ENDPOINT_URL")
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_KEY")
    AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-07-01-preview")

    EXCEL_FILE_PATH = os.getenv("EXCEL_FILE_PATH", "50rowsrealisticdata_CLEANED.xlsx")
    INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "healthcare_claims")
    EMBEDDING_MODEL = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYED_MODEL_NAME", "text-embedding-3-large")

    openai_client = AzureOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
        azure_endpoint=AZURE_OPENAI_ENDPOINT
    )

    if not all([AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY]):
        print("❌ Error: Missing required environment variables.")
        print("Please ensure the following are set in your .env file:")
        print("- AZURE_SEARCH_SERVICE_ENDPOINT")
        print("- AZURE_SEARCH_ADMIN_KEY")
        print("- ENDPOINT_URL")
        print("- AZURE_OPENAI_KEY")
        print("- EXCEL_FILE_PATH (optional, defaults to '50rowsrealisticdata_CLEANED.xlsx')")
        exit(1)

    if not os.path.exists(EXCEL_FILE_PATH):
        print(f"❌ Error: Excel file not found at {EXCEL_FILE_PATH}")
        exit(1)

    print("Starting Azure AI Search CMS-1500 healthcare claims indexing process...")
    index_cms1500_claims(
        excel_path=EXCEL_FILE_PATH,
        endpoint=AZURE_SEARCH_ENDPOINT,
        key=AZURE_SEARCH_KEY,
        openai_client=openai_client,
        index_name=INDEX_NAME,
        embedding_model=EMBEDDING_MODEL,
        batch_size=25
    )