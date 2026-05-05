import streamlit as st
import random

# Page config
st.set_page_config(page_title="😂 AI Age Predictor", page_icon="🎂")

# Title
st.title("🎂 Totally Accurate AI Age Predictor™")
st.write("Answer a few *very serious* questions and let our AI guess your age 🤖")

st.markdown("---")

# Inputs
name = st.text_input("👤 What’s your name?")
sleep = st.slider("😴 How many hours do you sleep?", 0, 12, 7)
coffee = st.slider("☕ Cups of coffee per day?", 0, 10, 2)
scroll = st.slider("📱 Hours of scrolling daily?", 0, 12, 3)

food = st.selectbox(
    "🍕 Pick your favorite food",
    ["Pizza", "Salad", "Instant Noodles", "Protein Shake", "Whatever is free"]
)

vibe = st.radio(
    "🧠 Your current vibe?",
    ["Chill", "Stressed", "Existential crisis", "Motivated for 5 mins"]
)

st.markdown("---")

# Prediction logic (fun, not real 😄)
if st.button("🔮 Predict My Age!"):
    if not name:
        st.warning("👀 Tell me your name first!")
    else:
        base_age = random.randint(18, 40)

        # Fun logic tweaks
        if coffee > 5:
            base_age += 5
        if sleep < 5:
            base_age += 3
        if scroll > 6:
            base_age -= 2
        if food == "Salad":
            base_age -= 3
        if vibe == "Existential crisis":
            base_age += 10

        # Clamp age
        predicted_age = max(10, min(80, base_age))

        # Funny messages
        messages = [
            "🧠 Our AI scanned your aura...",
            "📡 Reading your life signals...",
            "🤖 Consulting ancient algorithms...",
            "🔍 Analyzing your coffee-to-sleep ratio..."
        ]

        st.info(random.choice(messages))

        st.success(f"🎉 {name}, your predicted age is: **{predicted_age} years old!**")

        # Roast section 😂
        if predicted_age > 45:
            st.write("💀 You’ve seen things… probably dial-up internet.")
        elif predicted_age < 20:
            st.write("✨ You still believe life will work out. Cute.")
        else:
            st.write("😎 Perfect balance of chaos and responsibility.")

        st.balloons()

st.markdown("---")
st.caption("⚠️ This app is 100% nonsense. Do not use for life decisions.")