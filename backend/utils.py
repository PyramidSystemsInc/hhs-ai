import os
import json
import logging
import requests
import dataclasses
import httpx
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import QueryType

from typing import List, Dict, Any, Optional

DEBUG = os.environ.get("DEBUG", "false")
if DEBUG.lower() == "true":
    logging.basicConfig(level=logging.DEBUG)

AZURE_SEARCH_PERMITTED_GROUPS_COLUMN = os.environ.get(
    "AZURE_SEARCH_PERMITTED_GROUPS_COLUMN"
)


class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return super().default(o)


async def format_as_ndjson(r):
    try:
        async for event in r:
            yield json.dumps(event, cls=JSONEncoder) + "\n"
    except Exception as error:
        logging.exception("Exception while generating response stream: %s", error)
        yield json.dumps({"error": str(error)})


def parse_multi_columns(columns: str) -> list:
    if "|" in columns:
        return columns.split("|")
    else:
        return columns.split(",")


def fetchUserGroups(userToken, nextLink=None):
    # Recursively fetch group membership
    if nextLink:
        endpoint = nextLink
    else:
        endpoint = "https://graph.microsoft.com/v1.0/me/transitiveMemberOf?$select=id"

    headers = {"Authorization": "bearer " + userToken}
    try:
        r = requests.get(endpoint, headers=headers)
        if r.status_code != 200:
            logging.error(f"Error fetching user groups: {r.status_code} {r.text}")
            return []

        r = r.json()
        if "@odata.nextLink" in r:
            nextLinkData = fetchUserGroups(userToken, r["@odata.nextLink"])
            r["value"].extend(nextLinkData)

        return r["value"]
    except Exception as e:
        logging.error(f"Exception in fetchUserGroups: {e}")
        return []


def generateFilterString(userToken):
    # Get list of groups user is a member of
    userGroups = fetchUserGroups(userToken)

    # Construct filter string
    if not userGroups:
        logging.debug("No user groups found")

    group_ids = ", ".join([obj["id"] for obj in userGroups])
    return f"{AZURE_SEARCH_PERMITTED_GROUPS_COLUMN}/any(g:search.in(g, '{group_ids}'))"


def format_non_streaming_response(chatCompletion, history_metadata, apim_request_id):
    response_obj = {
        "id": chatCompletion.id,
        "model": chatCompletion.model,
        "created": chatCompletion.created,
        "object": chatCompletion.object,
        "choices": [{"messages": []}],
        "history_metadata": history_metadata,
        "apim-request-id": apim_request_id,
    }

    if len(chatCompletion.choices) > 0:
        message = chatCompletion.choices[0].message
        if message:
            if hasattr(message, "context"):
                response_obj["choices"][0]["messages"].append(
                    {
                        "role": "tool",
                        "content": json.dumps(message.context),
                    }
                )
            response_obj["choices"][0]["messages"].append(
                {
                    "role": "assistant",
                    "content": message.content,
                }
            )
            return response_obj

    return {}

def format_stream_response(chatCompletionChunk, history_metadata, apim_request_id):
    response_obj = {
        "id": chatCompletionChunk.id,
        "model": chatCompletionChunk.model,
        "created": chatCompletionChunk.created,
        "object": chatCompletionChunk.object,
        "choices": [{"messages": []}],
        "history_metadata": history_metadata,
        "apim-request-id": apim_request_id,
    }

    if len(chatCompletionChunk.choices) > 0:
        delta = chatCompletionChunk.choices[0].delta
        if delta:
            if hasattr(delta, "context"):
                messageObj = {"role": "tool", "content": json.dumps(delta.context)}
                response_obj["choices"][0]["messages"].append(messageObj)
                return response_obj
            if delta.role == "assistant" and hasattr(delta, "context"):
                messageObj = {
                    "role": "assistant",
                    "context": delta.context,
                }
                response_obj["choices"][0]["messages"].append(messageObj)
                return response_obj
            else:
                if delta.content:
                    messageObj = {
                        "role": "assistant",
                        "content": delta.content,
                    }
                    response_obj["choices"][0]["messages"].append(messageObj)
                    return response_obj

    return {}


def format_pf_non_streaming_response(
    chatCompletion, history_metadata, response_field_name, citations_field_name, message_uuid=None
):
    if chatCompletion is None:
        logging.error(
            "chatCompletion object is None - Increase PROMPTFLOW_RESPONSE_TIMEOUT parameter"
        )
        return {
            "error": "No response received from promptflow endpoint increase PROMPTFLOW_RESPONSE_TIMEOUT parameter or check the promptflow endpoint."
        }
    if "error" in chatCompletion:
        logging.error(f"Error in promptflow response api: {chatCompletion['error']}")
        return {"error": chatCompletion["error"]}

    logging.debug(f"chatCompletion: {chatCompletion}")
    try:
        messages = []
        if response_field_name in chatCompletion:
            messages.append({
                "role": "assistant",
                "content": chatCompletion[response_field_name] 
            })
        if citations_field_name in chatCompletion:
            citation_content= {"citations": chatCompletion[citations_field_name]}
            messages.append({ 
                "role": "tool",
                "content": json.dumps(citation_content)
            })

        response_obj = {
            "id": chatCompletion["id"],
            "model": "",
            "created": "",
            "object": "",
            "history_metadata": history_metadata,
            "choices": [
                {
                    "messages": messages,
                }
            ]
        }
        return response_obj
    except Exception as e:
        logging.error(f"Exception in format_pf_non_streaming_response: {e}")
        return {}


def convert_to_pf_format(input_json, request_field_name, response_field_name):
    output_json = []
    logging.debug(f"Input json: {input_json}")
    # align the input json to the format expected by promptflow chat flow
    for message in input_json["messages"]:
        if message:
            if message["role"] == "user":
                new_obj = {
                    "inputs": {request_field_name: message["content"]},
                    "outputs": {response_field_name: ""},
                }
                output_json.append(new_obj)
            elif message["role"] == "assistant" and len(output_json) > 0:
                output_json[-1]["outputs"][response_field_name] = message["content"]
    logging.debug(f"PF formatted response: {output_json}")
    return output_json


def comma_separated_string_to_list(s: str) -> List[str]:
    '''
    Split comma-separated values into a list.
    '''
    return s.strip().replace(' ', '').split(',')


async def perform_direct_search_query(
    endpoint: str, 
    key: str, 
    index_name: str, 
    query_text: str, 
    filter_str: str = None,
    top_k: int = 50,
    select: str = None,
    order_by: str = None
) -> Dict[str, Any]:
    """
    Directly query Azure Search without going through the OpenAI model.
    This allows performing direct aggregations and retrieving all documents.
    
    Args:
        endpoint: Azure Search endpoint
        key: Azure Search API key
        index_name: Name of the index to query
        query_text: The search query or "*" for all documents
        filter_str: Optional filter expression
        top_k: Number of documents to retrieve
        select: Comma-separated list of fields to return
        order_by: Field to order results by
    
    Returns:
        Dictionary with results and count
    """
    try:
        # Set up the search client
        credential = AzureKeyCredential(key)
        client = SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)
        
        # Execute the search query
        results = client.search(
            search_text=query_text,
            filter=filter_str,
            top=top_k,
            select=select.split(',') if select else None,
            order_by=order_by,
            query_type=QueryType.SIMPLE if query_text != "*" else None,
            include_total_count=True
        )
        
        # Convert results to a list
        result_list = list(results)
        
        # Return results along with total count
        return {
            "results": result_list,
            "count": results.get_count() 
        }
        
    except Exception as e:
        logging.error(f"Error performing direct search query: {str(e)}")
        raise


async def perform_search_aggregation(
    endpoint: str, 
    key: str, 
    index_name: str, 
    field: str,
    aggregation_type: str = "avg",
    filter_str: str = None,
    query_text: str = "*"
) -> Dict[str, Any]:
    """
    Perform aggregation operations directly on Azure Search data.
    
    Args:
        endpoint: Azure Search endpoint
        key: Azure Search API key
        index_name: Name of the index to query
        field: Field to aggregate on
        aggregation_type: Type of aggregation (avg, sum, min, max, count)
        filter_str: Optional filter expression
        query_text: The search query or "*" for all documents
    
    Returns:
        Dictionary with aggregation result
    """
    try:
        # Get all matching documents
        search_results = await perform_direct_search_query(
            endpoint=endpoint,
            key=key,
            index_name=index_name,
            query_text=query_text,
            filter_str=filter_str,
            top_k=1000,  # Use a high value to get as many documents as possible
            select=field
        )
        
        # Extract the values of the specified field
        values = [doc[field] for doc in search_results["results"] if field in doc and doc[field] is not None]
        
        # Ensure we have numeric values
        numeric_values = [float(val) for val in values if val is not None]
        
        if not numeric_values:
            return {"error": f"No numeric values found for field '{field}'"}
        
        # Perform the requested aggregation
        if aggregation_type.lower() == "avg":
            result = sum(numeric_values) / len(numeric_values)
        elif aggregation_type.lower() == "sum":
            result = sum(numeric_values)
        elif aggregation_type.lower() == "min":
            result = min(numeric_values)
        elif aggregation_type.lower() == "max":
            result = max(numeric_values)
        elif aggregation_type.lower() == "count":
            result = len(numeric_values)
        else:
            return {"error": f"Unsupported aggregation type: {aggregation_type}"}
        
        return {
            "result": result,
            "count": len(numeric_values),
            "aggregation_type": aggregation_type,
            "field": field
        }
        
    except Exception as e:
        logging.error(f"Error performing search aggregation: {str(e)}")
        raise


async def perform_analytics_query(
    endpoint: str,
    key: str,
    index_name: str,
    group_by_field: str,
    metric_field: str = None,
    metric_function: str = "count",
    filter_str: str = None,
    query_text: str = "*",
    top_results: int = 10,
    order: str = "desc"
) -> Dict[str, Any]:
    try:
        fields_to_select = [group_by_field]
        if metric_field and metric_field != group_by_field:
            fields_to_select.append(metric_field)
            
        select_str = ",".join(fields_to_select)
        
        search_results = await perform_direct_search_query(
            endpoint=endpoint,
            key=key,
            index_name=index_name,
            query_text=query_text,
            filter_str=filter_str,
            top_k=10000,  # Use a high value to get comprehensive results
            select=select_str
        )

        documents = search_results["results"]
        total_docs = search_results["count"]
        
        if not documents:
            return {
                "error": "No documents found matching the query"
            }

        groups = {}
        for doc in documents:
            if group_by_field not in doc or doc[group_by_field] is None:
                continue
                
            group_value = doc[group_by_field]

            if group_value not in groups:
                groups[group_value] = []

            groups[group_value].append(doc)

        results = []
        for group_value, group_docs in groups.items():
            result = {
                "group": group_value,
                "count": len(group_docs)
            }

            if metric_field and metric_function != "count":
                metric_values = []
                for doc in group_docs:
                    if metric_field in doc and doc[metric_field] is not None:
                        try:
                            metric_values.append(float(doc[metric_field]))
                        except (ValueError, TypeError):
                            # Skip values that can't be converted to float
                            pass
                
                # Calculate the metric
                if metric_values:
                    if metric_function == "sum":
                        result["metric"] = sum(metric_values)
                    elif metric_function == "avg":
                        result["metric"] = sum(metric_values) / len(metric_values)
                    elif metric_function == "min":
                        result["metric"] = min(metric_values)
                    elif metric_function == "max":
                        result["metric"] = max(metric_values)
                    else:
                        result["metric"] = len(metric_values)
                    
                    result["metric_function"] = metric_function
                    result["metric_field"] = metric_field
            
            results.append(result)

        if metric_function != "count" and any("metric" in r for r in results):
            results.sort(key=lambda x: x.get("metric", 0), reverse=(order.lower() == "desc"))
        else:
            results.sort(key=lambda x: x["count"], reverse=(order.lower() == "desc"))

        results = results[:top_results]
        
        return {
            "results": results,
            "total_groups": len(groups),
            "total_documents": total_docs,
            "group_by_field": group_by_field,
            "metric_function": metric_function,
            "metric_field": metric_field if metric_function != "count" else None
        }
        
    except Exception as e:
        logging.error(f"Error performing analytics query: {str(e)}")
        raise
