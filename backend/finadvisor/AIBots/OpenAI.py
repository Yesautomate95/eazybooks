from finadvisor.services.Functions import get_function_details, execute_function_call
from finadvisor.chatbot.constants import open_ai_client as client




def create_message_and_run(assistant, query, thread=None):
    if not thread:
        thread = client.beta.threads.create()

    message = client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=query
    )
    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant.id,
        temperature=0
    )
    return run, thread

def submit_tool_outputs(run, thread, function_id, function_response):
    run = client.beta.threads.runs.submit_tool_outputs(
        thread_id=thread.id,
        run_id=run.id,
        tool_outputs=[
            {
                "tool_call_id": function_id,
                "output": str(function_response),
            }
        ]
    )
    return run

async def fng(thread, run_id):

    while True:
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run_id)
        if run.status == "requires_action":
            function_name, arguments, function_id = get_function_details(run)

            function_response = await execute_function_call(function_name, arguments)

            run = submit_tool_outputs(run, thread, function_id, function_response)
            continue
        if run.status == "completed":
            messages = client.beta.threads.messages.list(thread_id=thread.id)
            latest_message = messages.data[0]
            response = latest_message.content[0].text.value
            return response
