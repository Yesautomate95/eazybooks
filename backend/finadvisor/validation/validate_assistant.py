import json
import asyncio

from finadvisor.chatbot.services.chat import ChatService

async def validate_assistant():
    # Load validation dataset
    with open("validation/validation_financial_dataset.json", "r") as f:
        validation_dataset = json.load(f)

    chat_service = ChatService()

    correct = 0
    total = len(validation_dataset)

    for item in validation_dataset:
        try:

            # Clear previous logs
            chat_service._logged_tool_calls = []

            # Generate the assistant's response
            async for _ in chat_service.generate("test_chat_id", item["prompt"]):
                pass  # Simulate execution

            # Retrieve the logged tool call
            logged_tool_calls = chat_service.get_logged_tool_calls()

            if not logged_tool_calls:
                continue

            # For simplicity, assuming one tool call per prompt
            called_function = logged_tool_calls[0]["function_name"]
            called_parameters = logged_tool_calls[0]["arguments"]

            # Compare with expected results
            if called_function == item["expected_function"] and called_parameters == item["expected_parameters"]:
                correct += 1

        except Exception as e:
            print(f"Error processing prompt: {item['prompt']}")

    accuracy = (correct / total) * 100
    print(f"Validation Accuracy: {accuracy}%")
    return accuracy


if __name__ == "__main__":
    asyncio.run(validate_assistant())
