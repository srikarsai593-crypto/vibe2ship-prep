import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

# 1. Initialize our environment and client
load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# 2. Define the actual "Hands" (Python functions) your app can execute
# IMPORTANT: Use Python Type Hints (like :str) so Gemini knows what data to send!
def schedule_focus_block(event_title: str, start_time: str, end_time: str) -> str:
    """
    Autonomously reserves a deep-work focus block in the user's calendar.
    Use this when the user needs to study or work on a critical task.
    """
    # This is where your real calendar code will go later. For now, we mock it:
    return f"✅ SUCCESS: Formatted and injected '{event_title}' from {start_time} to {end_time} into the database."

def clear_calendar_conflicts(justification: str) -> str:
    """
    Cancels lower-priority personal events (like watching TV or gaming) to clear immediate time for a crisis.
    """
    return f"💥 ACTION TAKEN: Cleared low-priority blocks. Reason: {justification}"


# 3. Test a chaotic user panic attack
user_panic_input = "I have a chemistry exam tomorrow morning and I haven't started. I'm supposed to watch a movie tonight, but I need to clear my schedule and study from 8 PM to 11 PM!"

print("🧠 Sending panic to Gemini...")

# 4. Define tools using the proper SDK format
tools = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="schedule_focus_block",
                description="Autonomously reserves a deep-work focus block in the user's calendar. Use this when the user needs to study or work on a critical task.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "event_title": types.Schema(type=types.Type.STRING, description="Title of the calendar event"),
                        "start_time": types.Schema(type=types.Type.STRING, description="Start time for the focus block"),
                        "end_time": types.Schema(type=types.Type.STRING, description="End time for the focus block"),
                    },
                    required=["event_title", "start_time", "end_time"],
                ),
            ),
            types.FunctionDeclaration(
                name="clear_calendar_conflicts",
                description="Cancels lower-priority personal events to clear immediate time for a crisis.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "justification": types.Schema(type=types.Type.STRING, description="Reason for clearing the calendar"),
                    },
                    required=["justification"],
                ),
            ),
        ]
    )
]

# 5. Pass the properly formatted tools into the tools list!
response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents=user_panic_input,
    config=types.GenerateContentConfig(
        system_instruction="You are The Last-Minute Life Saver agent. Be aggressive. Use tools to actively fix the user's schedule.",
        temperature=0.15,
        tools=tools
    )
)

# 6. The "Tool Catcher" Loop
# Check if Gemini decided it needs to run one of your Python functions
if response.function_calls:
    print("\n🚨 The AI Agent decided to take action!")
    
    for call in response.function_calls:
        print(f"👉 Gemini is calling the function: '{call.name}'")
        print(f"📦 Data Gemini generated for the function: {call.args}")
        
        # Execute the correct function based on what Gemini requested
        if call.name == "schedule_focus_block":
            # Extract values safely from Gemini's response payload
            result = schedule_focus_block(
                event_title=call.args.get("event_title"),
                start_time=call.args.get("start_time"),
                end_time=call.args.get("end_time")
            )
            print(result)
            
        elif call.name == "clear_calendar_conflicts":
            result = clear_calendar_conflicts(justification=call.args.get("justification"))
            print(result)
else:
    # If the user just said "Hi", Gemini won't call a tool, it will just talk
    print("\n💬 Gemini's Text Response:")
    print(response.text)