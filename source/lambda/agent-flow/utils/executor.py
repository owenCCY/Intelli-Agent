import json
from typing import Annotated, Any, TypedDict

from common_logic.common_utils.constant import LLMTaskType, ChatbotMode, MessageType
from common_logic.common_utils.exceptions import (
    ToolNotExistError,
    ToolParameterNotExistError,
    MultipleToolNameError,
    ToolNotFound
)
from common_logic.common_utils.lambda_invoke_utils import (
    invoke_lambda,
    is_running_local,
    node_monitor_wrapper,
    send_trace,
)
from common_logic.common_utils.python_utils import add_messages, update_nest_dict
from common_logic.common_utils.logger_utils import get_logger
from common_logic.common_utils.serialization_utils import JSONEncoder
from tools.tool_base import Tool, get_tool_by_name, tool_manager
from langgraph.graph import END, StateGraph
from utils.agent_base import build_agent_graph, tool_execution

from common_logic.common_utils.constant import (
    LLMTaskType,
    ToolRuningMode,
    SceneType,
    ChatbotMode
)

logger = get_logger('common_executor')


class ChatbotState(TypedDict):
    ########### input/output states ###########
    # inputs
    # origianl input question
    query: str
    # chat history between human and agent
    chat_history: Annotated[list[dict], add_messages]
    # complete chatbot config, consumed by all the nodes
    chatbot_config: dict
    # websocket connection id for the agent
    ws_connection_id: str
    # whether to enbale stream output via ws_connection_id
    stream: bool
    # message id related to original input question
    message_id: str = None
    # record running states of different nodes
    trace_infos: Annotated[list[str], add_messages]
    # whether to enbale trace info update via streaming ouput
    enable_trace: bool
    # outputs
    # final answer generated by whole app graph
    answer: Any
    # information needed return to user, e.g. intention, context, figure and so on, anything you can get during execution
    extra_response: Annotated[dict, update_nest_dict]

    ########### query rewrite states ###########
    # query rewrite results
    query_rewrite: str = None

    ########### intention detection states ###########
    # intention type of retrieved intention samples in search engine, e.g. OpenSearch
    intent_type: str = None
    # retrieved intention samples in search engine, e.g. OpenSearch
    intent_fewshot_examples: list
    # tools of retrieved intention samples in search engine, e.g. OpenSearch
    intent_fewshot_tools: list

    ########### retriever states ###########
    # contexts information retrieved in search engine, e.g. OpenSearch
    qq_match_results: list = []
    contexts: str = None
    figure: list = None

    ########### agent states ###########
    # current output of agent
    agent_current_output: dict
    # record messages during agent tool choose and calling, including agent message, tool ouput and error messages
    agent_tool_history: Annotated[list[dict], add_messages]
    # the maximum number that agent node can be called
    agent_repeated_call_limit: int
    # the current call time of agent
    agent_current_call_number: int  #
    # whehter the current call time is less than maximum number of agent call
    agent_repeated_call_validation: bool
    # function calling
    # whether the output of agent can be parsed as the valid tool calling
    function_calling_parse_ok: bool
    # whether the current parsed tool calling is run once
    function_calling_is_run_once: bool
    # current tool calls
    function_calling_parsed_tool_calls: list


####################
# nodes in graph #
####################

@node_monitor_wrapper
def query_preprocess(state: ChatbotState):
    output: str = invoke_lambda(
        event_body=state,
        lambda_name="Online_Query_Preprocess",
        lambda_module_path="lambda_query_preprocess.query_preprocess",
        handler_name="lambda_handler",
    )

    send_trace(f"\n\n**query_rewrite:** \n{output}", state["stream"], state["ws_connection_id"], state["enable_trace"])
    return {"query_rewrite": output}


@node_monitor_wrapper
def intention_detection(state: ChatbotState):
    # retriever_params = state["chatbot_config"]["intention_retrievers"]
    # retriever_params["query"] = state["query"]
    # output: str = invoke_lambda(
    #     event_body=retriever_params,
    #     lambda_name="Online_Functions",
    #     lambda_module_path="functions.functions_utils.retriever.retriever",
    #     handler_name="lambda_handler",
    # )
    #
    context_list = []
    #
    # qq_match_threshold = retriever_params['retriever_config']['threshold']
    #
    # for doc in output["result"]["docs"]:
    #     if doc['retrieval_score'] > qq_match_threshold:
    #         send_trace(f"\n\n**similar query found**\n", state["stream"], state["ws_connection_id"],
    #                    state["enable_trace"])
    #         query_content = doc['answer']
    #         return {
    #             "answer": query_content,
    #             "intent_type": "similar query found",
    #         }
    #     question = doc['question']
    #     answer = doc['answer']
    #     context_list.append(f"Question: {question}, \nAnswer：{answer}")

    intent_fewshot_examples = invoke_lambda(
        lambda_module_path="lambda_intention_detection.intention",
        lambda_name="Online_Intention_Detection",
        handler_name="lambda_handler",
        event_body=state,
    )

    intent_fewshot_tools: list[str] = list(
        set([e["intent"] for e in intent_fewshot_examples])
    )

    send_trace(
        f"**intention retrieved:**\n{json.dumps(intent_fewshot_examples, ensure_ascii=False, indent=2)}",
        state["stream"], state["ws_connection_id"], state["enable_trace"])
    return {
        "intent_fewshot_examples": intent_fewshot_examples,
        "intent_fewshot_tools": intent_fewshot_tools,
        "qq_match_results": context_list,
        "intent_type": "intention detected",
    }


@node_monitor_wrapper
def agent(state: ChatbotState):
    # two cases to invoke rag function
    # 1. when valid intention fewshot found
    # 2. for the first time, agent decides to give final results

    # deal with once tool calling
    if state['agent_repeated_call_validation'] and state['function_calling_parse_ok'] and state['agent_tool_history']:
        tool_execute_res = state['agent_tool_history'][-1]['additional_kwargs']['raw_tool_call_results'][0]
        tool_name = tool_execute_res['name']
        output = tool_execute_res['output']
        tool = get_tool_by_name(tool_name, scene=SceneType.COMMON)
        if tool.running_mode == ToolRuningMode.ONCE:
            send_trace("once tool", enable_trace=state["enable_trace"])
            return {
                "answer": output['result'],
                "function_calling_is_run_once": True
            }

    no_intention_condition = not state['intent_fewshot_examples']
    first_tool_final_response = False
    if (state['agent_current_call_number'] == 1) and state['function_calling_parse_ok'] and state['agent_tool_history']:
        tool_execute_res = state['agent_tool_history'][-1]['additional_kwargs']['raw_tool_call_results'][0]
        tool_name = tool_execute_res['name']
        if tool_name == "give_final_response":
            first_tool_final_response = True

    # TODO: double check only_use_rag_tool
    if no_intention_condition or first_tool_final_response:
        # if state['chatbot_config']['agent_config']['only_use_rag_tool']:
        #     send_trace("agent only use rag tool", enable_trace=state["enable_trace"])
        if no_intention_condition:
            send_trace("no_intention_condition, switch to rag tool", enable_trace=state["enable_trace"])
        elif first_tool_final_response:
            send_trace("first tool is final response, switch to rag tool", enable_trace=state["enable_trace"])

        return {
            "function_calling_parse_ok": True,
            "agent_repeated_call_validation": True,
            "function_calling_parsed_tool_calls": [{
                "name": "rag_tool",
                "kwargs": {},
                "model_id": state['chatbot_config']['agent_config']['model_id']
            }]
        }
    response = app_agent.invoke(state)

    return response


# @node_monitor_wrapper
# def llm_direct_results_generation(state: ChatbotState):
#     group_name = state['chatbot_config']['group_name']
#     llm_config = state["chatbot_config"]["chat_config"]
#     task_type = LLMTaskType.CHAT
#
#     # prompt_templates_from_ddb = get_prompt_templates_from_ddb(
#     #     group_name,
#     #     model_id=llm_config['model_id'],
#     # ).get(task_type, {})
#     # logger.info(prompt_templates_from_ddb)
#     system_prompt = "TODO"
#
#     answer: dict = invoke_lambda(
#         event_body={
#             "llm_config": {
#                 **llm_config,
#                 "stream": state["stream"],
#                 "intent_type": task_type,
#                 "system_prompt": system_prompt,
#             },
#             "llm_input": {
#                 "query": state["query"],
#                 "chat_history": state["chat_history"],
#
#             },
#         },
#         lambda_name="Online_LLM_Generate",
#         lambda_module_path="lambda_llm_generate.llm_generate",
#         handler_name="lambda_handler",
#     )
#     return {"answer": answer}


def final_results_preparation(state: ChatbotState):
    return {"answer": state['answer']}


def matched_query_return(state: ChatbotState):
    return {"answer": state["answer"]}


################
# define edges #
################

# def query_route(state: dict):
#     # return f"{state['chatbot_config']['chatbot_mode']} mode"
#     return "agent mode"


def intent_route(state: dict):
    return state["intent_type"]


def agent_route(state: dict):
    if state.get("function_calling_is_run_once", False):
        return "no need tool calling"

    state["agent_repeated_call_validation"] = state['agent_current_call_number'] < state['agent_repeated_call_limit']

    if state["agent_repeated_call_validation"]:
        return "valid tool calling"
    else:
        # TODO give final strategy
        raise RuntimeError


#############################
# define online top-level graph for app #
#############################

def build_graph(chatbot_state_cls):
    workflow = StateGraph(chatbot_state_cls)
    # add node for all chat/rag/agent mode
    workflow.add_node("query_preprocess", query_preprocess)
    # chat mode
    # workflow.add_node("llm_direct_results_generation", llm_direct_results_generation)
    # rag mode
    # workflow.add_node("knowledge_retrieve", knowledge_retrieve)
    # workflow.add_node("llm_rag_results_generation", llm_rag_results_generation)
    # agent mode
    workflow.add_node("intention_detection", intention_detection)
    workflow.add_node("matched_query_return", matched_query_return)
    # agent sub graph
    workflow.add_node("agent", agent)
    workflow.add_node("tools_execution", tool_execution)
    workflow.add_node("final_results_preparation", final_results_preparation)

    # add all edges
    workflow.set_entry_point("query_preprocess")
    # chat mode
    # workflow.add_edge("llm_direct_results_generation", END)
    # rag mode
    # workflow.add_edge("knowledge_retrieve", "llm_rag_results_generation")
    # workflow.add_edge("llm_rag_results_generation", END)
    # agent mode
    workflow.add_edge("tools_execution", "agent")
    workflow.add_edge("query_preprocess", "intention_detection")
    workflow.add_edge("matched_query_return", "final_results_preparation")
    workflow.add_edge("final_results_preparation", END)

    # add conditional edges
    # choose running mode based on user selection:
    # 1. chat mode: let llm generate results directly
    # 2. rag mode: retrive all knowledge and let llm generate results
    # 3. agent mode: let llm generate results based on intention detection, tool calling and retrieved knowledge
    # workflow.add_conditional_edges(
    #     "query_preprocess",
    #     query_route,
    #     {
    #         "chat mode": "llm_direct_results_generation",
    #         "agent mode": "intention_detection",
    #     },
    # )

    # three running branch will be chosen based on intention detection results:
    # 1. similar query found: if very similar queries were found in knowledge base, these queries will be given as results
    # 2. intention detected: if intention detected, the agent logic will be invoked
    workflow.add_conditional_edges(
        "intention_detection",
        intent_route,
        {
            "similar query found": "matched_query_return",
            "intention detected": "agent",
        },
    )

    # the results of agent planning will be evaluated and decide next step:
    # 1. valid tool calling: the agent chooses the valid tools, and the tools will be executed
    # 2. no need tool calling: the agent thinks no tool needs to be called, the final results can be generated
    workflow.add_conditional_edges(
        "agent",
        agent_route,
        {
            "valid tool calling": "tools_execution",
            "no need tool calling": "final_results_preparation",
        },
    )

    app = workflow.compile()
    return app


#####################################
# define online sub-graph for agent #
#####################################
app_agent = None
app = None


def run(event_body):
    """
    Entry point for the Lambda function.
    :param event_body: The event body for lambda function.
    return: answer(str)
    """
    global app, app_agent
    if app is None:
        app = build_graph(ChatbotState)

    if app_agent is None:
        app_agent = build_agent_graph(ChatbotState)

    # debuging
    # TODO only write when run local
    if is_running_local():
        with open("common_entry_workflow.png", "wb") as f:
            f.write(app.get_graph().draw_png())

        with open("common_entry_agent_workflow.png", "wb") as f:
            f.write(app_agent.get_graph().draw_png())

    ################################################################################
    # prepare inputs and invoke graph
    logger.info(f'event_body:\n{json.dumps(event_body, ensure_ascii=False, indent=2, cls=JSONEncoder)}')

    chatbot_config = event_body["chatbot_config"]
    query = event_body["query"]
    use_history = event_body.get("use_history", True)
    chat_history = event_body.get("chat_history", []) if use_history else []
    stream = event_body.get("stream", True)
    message_id = event_body["custom_message_id"]
    ws_connection_id = event_body["ws_connection_id"]
    enable_trace = event_body.get("enable_trace", True)
    # get all registered tools with parameters
    # valid_tool_calling_names = tool_manager.get_names_from_tools_with_parameters()
    for retriever in chatbot_config["knowledge_base_retrievers"]:
        tool_manager.register_rag_tool(retriever["name"], retriever["description"])

    # invoke graph and get results
    response = app.invoke(
        {
            "stream": stream,
            "chatbot_config": chatbot_config,
            "query": query,
            "enable_trace": enable_trace,
            "trace_infos": [],
            "message_id": message_id,
            "chat_history": chat_history,
            "agent_tool_history": [],
            "ws_connection_id": ws_connection_id,
            "debug_infos": {},
            "extra_response": {},
            "agent_repeated_call_limit": 5,
            "agent_current_call_number": 0,
        }
    )

    return {"answer": response["answer"], **response["extra_response"]}


if __name__ == '__main__':
    event_body = {
        "query": "Hello",
        # "entry_type": "common",
        "user_id": "b85163d0-6011-70fd-216e-d1d6d64f153d",
        "session_id": "cfa48a53-5e6a-4942-a900-639b1b48d7da",
        "use_history": True,
        "enable_trace": True,
        "use_websearch": True,
        "stream": False,
        "chat_history": [],
        "ws_connection_id": None,
        "custom_message_id": "",
        "ddb_history_obj": [],
        "request_timestamp": 1721109343.02637,
        "chatbot_config": {
            "bot_id": "a1b2",
            "version": "TEST",
            "description": "Test Bot",
            "knowledge_base_retrievers": [
                {
                    "name": "QA",
                    "description": "Answer question based on search result",
                    "index": "test-qa",
                    "config": {
                        "top_k": "3",
                        "vector_field_name": "sentence_vector",
                        "text_field_name": "paragraph",
                        "source_field_name": "source",
                        "use_hybrid_search": "true",
                    },
                    "embedding": {
                        "type": "Bedrock",
                        "model_id": "amazon.titan-embed-text-v2:0"
                    }
                }
            ],
            "intention_retrievers": [
                {
                    "index": "test-intent3",
                    "config": {
                        "top_k": "3"
                    },
                    "embedding": {
                        "type": "Bedrock",
                        "model_id": "cohere.embed-english-v3"
                    }
                }
            ],
            "llm": {
                "type": "Bedrock",
                "model_id": "anthropic.claude-3-sonnet-20240229-v1:0",
                "model_kwargs": {
                    "temperature": 0.0,
                    "max_tokens": 4096
                }
            },
            "prompts": [
                {
                    "type": "RAG",
                    "text": "You are a customer service chatbot. You ALWAYS follow these guidelines when writing your response to user's query:\n<guidelines>\n- NERVER say \"\u6839\u636e\u641c\u7d22\u7ed3\u679c/\u5927\u5bb6\u597d/\u8c22\u8c22...\".\n</guidelines>\n\nHere are some documents for you to reference for your query.\n<docs>\n{context}\n</docs>"},
                {
                    "type": "GENERAL",
                    "text": "You are a customer service chatbot."
                },
                {
                    "type": "CONV_SUMMARY",
                    "text": "Given the following conversation between `USER` and `AI`, and a follow up `USER` reply, Put yourself in the shoes of `USER`, rephrase the follow up `USER` reply to be a standalone reply.\n\nChat History:\n{history}\n\nThe USER's follow up reply: {question}"
                }
            ],
            "tools": [
                {
                    "name": "get_weather"
                },
                {
                    "name": "comfort"
                }
            ]
        },

    }
    resp = run(event_body)
    print("Final response> ", resp)
