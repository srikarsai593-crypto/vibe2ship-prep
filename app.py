import streamlit as st
import requests
import os
from google import genai
from dotenv import load_dotenv

# 1. Access secure background environmental credentials
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

# 2. Instantiate the canonical Google GenAI SDK system client
client = genai.Client(api_key=api_key)

# 3. Setup structural frontend interface layout configurations
st.set_page_config(page_title="AI Weather Assistant", page_icon="🌦️")
st.title("AI-Powered Weather Planner 🌦️")

# 4. Handle reactive user string context inputs
city_input = st.text_input("Enter City Name:", "Anantapur")

if st.button("Get Weather Insights"):
    # Sanitize user entries by stripping accidental leading/trailing blank whitespaces
    clean_city = city_input.strip()

    # FIX: Added the missing '/' between the domain and city name
    # Before: f"https://wttr.in{clean_city}"  → resolves to 'wttr.inanantapur' (invalid host)
    # After:  f"https://wttr.in/{clean_city}" → resolves to 'wttr.in/anantapur' (correct)
    weather_url = f"https://wttr.in/{clean_city}"
    query_params = {"format": "j1"}

    try:
        # Request localized tracking records from open network node
        response = requests.get(weather_url, params=query_params)

        if response.status_code == 200:
            data = response.json()
            current = data['current_condition'][0]
            temp = current['temp_C']
            desc = current['weatherDesc'][0]['value']

            # Render layout interface metrics blocks
            st.metric(label=f"Current Temp in {clean_city}", value=f"{temp}°C", delta=desc)

            # Configure distinct structured situational data prompts
            prompt = f"The current weather in {clean_city} is {temp}°C with clear conditions described as '{desc}'. Provide a definitive, actionable three-sentence lifestyle summary detailing optimal clothing styles and scheduling advice based on this current data status."

            with st.spinner("Gemini framework engine parsing response..."):
                # Execute inference directly using standard 2.5 Flash operational profiles
                ai_response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                )
                st.subheader("AI Recommendation:")
                st.write(ai_response.text)
        else:
            st.error(f"Target location tracking database returned validation error code: {response.status_code}")

    except Exception as e:
        st.error(f"An isolated communication boundary failure transpired: {e}")