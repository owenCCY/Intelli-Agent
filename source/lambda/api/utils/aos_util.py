import os
import json
import requests
import boto3
from utils.secret_util import get_secret_value
from requests.auth import HTTPBasicAuth
from model.model import Intention, BotVersion
from utils.embedding_util import create_aos_ingestion, get_embedding_function
from utils.common import get_index, get_index_and_model_id
from aws_lambda_powertools import Logger
from langchain.docstore.document import Document
from langchain_community.vectorstores import OpenSearchVectorSearch
from langchain_community.vectorstores.opensearch_vector_search import (
    OpenSearchVectorSearch
)
from opensearchpy import RequestsHttpConnection
from opensearchpy import OpenSearch

aos_domain_name = os.environ.get("AOS_DOMAIN_NAME", "smartsearch")
secret_name = os.environ.get('AOS_SECRET_NAME')
region = os.environ.get("AWS_REGION")

secret = json.loads(get_secret_value(secret_name))
username = secret.get("username")
password = secret.get("password")

HTTPS_PORT_NUMBER = "443"

headers = {'Content-Type': 'application/json'}

logger = Logger()

class AOSUtil:
    def __init__(
        self
    ):
        self.opensearch_endpoint = self.get_aos_endpoint()
        self.aos_client = self.get_aos_client(self.opensearch_endpoint, HTTPS_PORT_NUMBER)

    def get_aos_endpoint(self):
        aos_client = boto3.client('opensearch')
        response = aos_client.describe_domain(
            DomainName=aos_domain_name
        )

        aos_endpoint = response['DomainStatus']['Endpoint']
        return aos_endpoint
    
    def get_aos_client(self, host, port):
        client = OpenSearch(
            hosts = [{'host': host, 'port': port}],
            http_auth = HTTPBasicAuth(username, password),
            use_ssl = True,
            verify_certs = True,
            connection_class=RequestsHttpConnection
        )
        return client

    def add_doc(self, bot_id: str, body: Intention):

        index, model_id = get_index_and_model_id(bot_id, version=BotVersion.TEST)

        question = body.question
        answer = body.answer
        intention = answer.intent
        keyword_argument = answer.kwargs
        
        doc = Document(page_content=question, metadata={'content_type': 'qq', 'source': 'api', 'jsonlAnswer': {'intent': intention, 'kwargs': keyword_argument}})
    
        embedding_function = get_embedding_function(
            region,
            model_id
        )
        
        docsearch = OpenSearchVectorSearch(
            index_name=index,
            embedding_function=embedding_function,
            opensearch_url=f'https://{self.opensearch_endpoint}',
            http_auth=(username, password),
            timeout=300,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )
        
        create_aos_ingestion(model_id, docsearch, [doc])
    

    def list_doc(self, bot_id: str, version: str, start_from: int, size: int):

        index = get_index(bot_id, version)

        intention_list = []

        search_body = {
            "query": {
                "match_all": {}
            }
        }

        result = self.aos_client.search(
            body=search_body, 
            index=index, 
            params={
                'from': start_from,
                'size': size
            }, 
            headers=headers
        )

        hits = result.get("hits")
        
        for hit in hits.get("hits"):
            intention_dict={}
            intention_dict["_id"] = hit.get("_id")
            source=hit.get("_source")
            intention_dict["text"] = source.get("text")
            meta=source.get("metadata")
            jsonl_answer=meta.get("jsonlAnswer")
            intention_dict["intent"] = jsonl_answer.get("intent")
            intention_dict["kwargs"] = jsonl_answer.get("kwargs")
            intention_list.append(intention_dict)

        return intention_list

    def update_doc(self, bot_id: str, version: str, body: dict, intention_id: str):

        index = get_index(bot_id, version)
        
        question = body.get("question")
        answer = body.get("answer")
        new_doc = {"doc": {"text": question, "metadata": {"jsonlAnswer": answer}}}

        response = self.aos_client.update(
            index=index,
            id=intention_id,
            body=new_doc
        )

        return response

    def delete_doc(self, bot_id: str, version: str, intention_id: str):

        index = get_index(bot_id, version)

        response = self.aos_client.delete(
            index = index,
            id = intention_id
        )

        return response