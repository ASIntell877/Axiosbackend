import streamlit as st
import requests
import uuid

# Replace this with your actual FastAPI backend URL
API_URL = "http://localhost:8000/chat"

def main():
    st.title("St. Maximos Chatbot")

    # Client selector (for multi-client support)
    client_id = st.selectbox("Select Client", options=["maximos"])

    # Generate a unique chat session ID, or keep it per user
    if "chat_id" not in st.session_state:
        st.session_state.chat_id = str(uuid.uuid4())

    user_question = st.text_input("Ask your question:")

    if st.button("Send") and user_question:
        # Prepare payload
        payload = {
            "chat_id": st.session_state.chat_id,
            "client_id": client_id,
            "question": user_question
        }

        try:
            response = requests.post(API_URL, json=payload)
            response.raise_for_status()
            data = response.json()

            # Show the chatbot's answer
            st.markdown("**St. Maximos says:**")
            st.write(data.get("answer", "No answer found."))

            # Show source docs if any
            source_docs = data.get("source_documents", [])
            if source_docs:
                st.markdown("### Source Documents:")
                for doc in source_docs:
                    st.markdown(f"- **{doc['source']}**: {doc['text']}...")

        except Exception as e:
            st.error(f"Error communicating with backend: {e}")

if __name__ == "__main__":
    main()
