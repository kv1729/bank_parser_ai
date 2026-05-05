import streamlit as st
import random
import time

st.set_page_config(page_title="🤡 AI Age Predictor", page_icon="🎂")

# Header
st.title("🤡 Ultra-Accurate AI Age Predictor™")
st.subheader("Powered by vibes, chaos, and questionable science")

st.markdown("---")

# Step 1: Name
name = st.text_input("👤 Enter your name (no fake names… we will know 👀)")

# Step 2: Fun questions
sleep = st.slider("😴 Sleep hours (be honest…)", 0, 12, 6)
coffee = st.slider("☕ Coffee per day (fuel level)", 0, 10, 2)
scroll = st.slider("📱 Daily scrolling (doom level)", 0, 12, 4)

food = st.selectbox(
    "🍕 What do you eat most?",
    ["Pizza 🍕", "Salad 🥗", "Maggi 🍜", "Protein shake 💪", "Whatever is free 🥲"]
)

vibe = st.radio(
    "🧠 Your current mental state:",
    ["Chill 😎", "Stressed 😵", "Existential crisis 💀", "Motivated for 7 minutes 🚀"]
)

lied = st.checkbox("🤥 I have lied in at least one answer")

st.markdown("---")

# Button
if st.button("🔮 Reveal My Destiny (Age)"):
    
    if not name:
        st.warning("🚨 You thought you could stay anonymous? Enter your name.")
        st.stop()

    # Dramatic loading
    with st.spinner("🧠 AI is judging your life choices..."):
        time.sleep(1.5)
        st.write("📡 Scanning your aura...")
        time.sleep(1)
        st.write("🔍 Calculating emotional damage...")
        time.sleep(1)
        st.write("☕ Measuring caffeine dependency...")
        time.sleep(1)

    # Base random
    age = random.randint(16, 45)

    # Logic tweaks (fun)
    age += coffee // 2
    age -= sleep // 3
    age += scroll // 2

    if food == "Salad 🥗":
        age -= 5
    elif food == "Maggi 🍜":
        age += 4
    elif food == "Whatever is free 🥲":
        age += 6

    if vibe == "Existential crisis 💀":
        age += 12
    elif vibe == "Motivated for 7 minutes 🚀":
        age -= 2

    if lied:
        age += 7

    # Clamp
    age = max(10, min(85, age))

    # Reveal
    st.markdown("## 🎉 RESULT TIME 🎉")
    st.success(f"🧬 {name}, our AI predicts your age is **{age} years old**")

    # Funny reactions
    if age > 50:
        st.write("💀 You've unlocked *back pain without reason* DLC.")
    elif age > 35:
        st.write("📉 You’ve started saying: 'I’ll sleep early today' (you won’t).")
    elif age > 25:
        st.write("😎 You’re in your 'figuring life out but also not really' era.")
    elif age > 18:
        st.write("🔥 Peak chaos. Maximum confusion. Minimal savings.")
    else:
        st.write("✨ You still think 30 is old. Adorable.")

    # Extra roast
    roasts = [
        "🤖 Our AI suggests drinking water. Revolutionary advice.",
        "📊 87% of your personality is based on memes.",
        "🧠 Brain usage detected: intermittent.",
        "☕ You are 42% caffeine at this point.",
        "📱 Screen time has entered 'concerning' territory."
    ]

    st.info(random.choice(roasts))

    # Bonus: fake confidence
    confidence = random.randint(60, 99)
    st.write(f"📈 AI Confidence: **{confidence}%** (we made this up)")

    st.balloons()

st.markdown("---")
st.caption("⚠️ This app is completely nonsense. If it's accurate… that’s between you and destiny.")