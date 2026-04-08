from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
import requests
from .models import SearchLog
from user_agents import parse
from django.core.cache import cache
from django.contrib.auth.models import User

API_KEY = "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"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

headers = { #for sending API's Key
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

MAX_MEMORY = 8

def home(request):
    return render(request, "chat/index.html")

def is_mood_related(text):
    text = text.lower()
    keywords = [
        "sad", "happy", "angry", "stress", "anxious",
        "tired", "lonely", "empty", "numb",
        "overthinking", "fear", "worry",
        "confused", "lost", "pressure",
        "fine", "okay", "good", "not good",
        "bad", "normal", "meh"
    ]
    return any(word in text for word in keywords)


def detect_emotion(text):
    text = text.lower()
    if any(w in text for w in [
"angry", "mad", "irritated", "frustrated",
"annoyed", "pissed", "hate this",
"fed up", "done with this",
"why is this happening",
"this is unfair", "so annoying"
]):
        return "angry"
    if any(w in text for w in ["sad", "down", "low", "depressed", "unhappy",
"lonely", "alone", "empty", "numb",
"tired of everything", "no energy", "exhausted",
"nothing feels good", "lost interest",
"i feel like crying", "want to cry",
"hopeless", "worthless", "no purpose",
"feel useless", "not good enough"]):
        return "sad"
    if any(w in text for w in [
"anxious", "worried", "stress", "stressed",
"overthinking", "panic", "nervous",
"can't relax", "mind racing",
"what if", "scared about future",
"fear", "pressure", "too much in my head",
"can't stop thinking"
]):
        return "anxious"
    if any(w in text for w in [
"confused", "lost", "dont know", "don't know",
"no idea", "what to do",
"stuck", "directionless",
"nothing makes sense",
"i feel blank", "i am clueless"
]):
        return "confused"
    if any(w in text for w in [
"happy", "good", "fine", "great",
"feeling better", "okay now",
"doing well", "peaceful",
"relaxed", "content"
]):
        return "happy"
    return "neutral"

def query(messages):
    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": messages
    }
    try:
        res = requests.post(
            API_URL,
            headers=headers,
            json=payload,
            timeout=10
        )
        print("STATUS:", res.status_code)
        print("RESPONSE:", res.text)

        if res.status_code != 200:
            return {"error": res.text}

        data = res.json()

        if "choices" not in data:
            return {"error": data}

        return data

    except requests.exceptions.Timeout:
        return {"error": "Timeout"}
    except Exception as e:
        return {"error": str(e)}

def chatbot(request):
    global stage
    user_msg = request.GET.get("message") #takes message from frontend
    conversation = request.session.get("conversation", []) #chat memory store karta hai
    stage = request.session.get("stage", "start")
    user_ip = request.META.get('HTTP_X_FORWARDED_FOR') #gets user IP(multiple login request na bhej paaye)
    if user_ip:
        user_ip = user_ip.split(',')[0]
    else:
        user_ip = request.META.get('REMOTE_ADDR')
    count = cache.get(user_ip, 0)
    if count > 20:
        return JsonResponse({"reply": "Too many requests. Slow down."})

    cache.set(user_ip, count + 1, timeout=60)
    session_id = request.session.session_key #unique user session
    if not session_id:
        request.session.create()
        session_id = request.session.session_key

    ip = request.META.get('HTTP_X_FORWARDED_FOR')
    if ip:
        ip = ip.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    location = request.session.get("location")

    if not location:
        city, country = get_location(ip) #Calls API to detect location from IP.
        request.session["location"] = (city, country)
    else:
        city, country = location

    ua_string = request.META.get('HTTP_USER_AGENT')
    ua = parse(ua_string)

    device = "Mobile" if ua.is_mobile else "PC"
    browser = ua.browser.family
    os = ua.os.family
    SearchLog.objects.create(
        session_id=session_id,
        message=user_msg,
        user_ip=ip,
        user_agent=request.META.get("HTTP_USER_AGENT"),
        device=device,
        browser=browser,
        os=os,
        city=city,
        country=country
    )

    if not user_msg:
        return JsonResponse({"reply": "Say something."})

    if not is_mood_related(user_msg) and stage == "start":
        return JsonResponse({
            "reply": "I’m here to talk about how you feel. Tell me what’s going on inside you."
        })

    emotion = detect_emotion(user_msg)
    conversation.append({"role": "user", "content": user_msg})

    if len(conversation) > MAX_MEMORY:
        conversation.pop(0)

    # ---------------- STAGE 1 ----------------
    if stage == "start":

        system_prompt = f"""
You are a calm, emotionally intelligent AI.

User emotion: {emotion}

Goal:
Understand the user deeply.

Rules:
- Talk naturally like a human
- Ask 1 meaningful question
- No robotic tone
- No forced structure
- Keep it short but thoughtful

Example:
"What’s been on your mind lately?"
"""

        stage = "reason"

    # ---------------- STAGE 2 ----------------
    elif stage == "reason":
        system_prompt = f"""
You are a supportive and thoughtful AI.

User emotion: {emotion}

User has shared something.

Goal:
Make them feel understood.

Rules:
- Start by understanding their feeling
- Respond like a real human, not a template
- Give a small insight if it fits
- Ask 1 natural follow-up question
- No fixed structure
- No generic lines

Talk like a real person who actually cares.
"""
        stage = "advice"

    # ---------------- STAGE 3 ----------------
    else:
        system_prompt = f"""
You are a calm and grounded AI.

User emotion: {emotion}

Rules:
- Keep responses natural and real
- Give insight only if useful
- Don’t sound like a motivational speaker
- Don’t repeat yourself
- Keep it short
- Ask a question only if it feels natural
"""

    messages = [{"role": "system", "content": system_prompt}] + conversation

    output = query(messages)
    if not output:
        return JsonResponse({"reply": "No response from server."})

    if "error" in output:
        return JsonResponse({"reply": str(output["error"])})

    if "choices" not in output:
        return JsonResponse({"reply": "Invalid API response."})


    if output and "choices" in output:
        reply = output["choices"][0]["message"]["content"]
    else:
        reply = "Something went wrong. Try again."

    conversation.append({"role": "assistant", "content": reply}) #saves the reply

    request.session["conversation"] = conversation
    request.session["stage"] = stage
    return JsonResponse({"reply": reply})
from django.db.models import Count
def dashboard(request):
    total_users = SearchLog.objects.values("session_id").distinct().count()
    total_messages = SearchLog.objects.count()

    top_messages = (
        SearchLog.objects.values("message")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    return render(request, "chat/dashboard.html", {
        "users": total_users,
        "messages": total_messages,
        "top": top_messages
    })

def get_location(ip):
    try:
        res = requests.get(f"http://ip-api.com/json/{ip}")
        data = res.json()
        return data.get("city", "unknown"), data.get("country", "unknown")
    except:
        return "unknown", "unknown"


def create_admin(request):
    user, created = User.objects.get_or_create(username='admin')

    user.set_password('admin123')
    user.is_staff = True
    user.is_superuser = True
    user.save()
    return HttpResponse("Admin password reset done")
