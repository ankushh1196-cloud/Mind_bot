from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
import requests
from .models import SearchLog
from user_agents import parse
from django.core.cache import cache
from django.contrib.auth.models import User
from django.db.models import Count


API_KEY = "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"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

headers = { #for sending API's Key
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

conversation = [] #stores chat history of past 8 messages
stage = "start"
MAX_MEMORY = 8

def home(request):
    return render(request, "chat/index.html")

def is_mood_related(text):
    text = text.lower()
    keywords = [
        "sad", "happy", "angry", "stress", "anxious",
        "feeling", "emotion", "tired", "lonely",
        "depressed", "upset", "hurt", "confused",
        "frustrated", "overthinking", "fear", "worry"
    ]
    return any(word in text for word in keywords)


def detect_emotion(text):
    text = text.lower()

    if any(w in text for w in ["angry", "hate", "frustrated"]):
        return "angry"
    if any(w in text for w in ["sad", "depressed", "lonely", "hurt"]):
        return "sad"
    if any(w in text for w in ["anxious", "worried", "stress"]):
        return "anxious"
    if any(w in text for w in ["confused", "lost", "dont know"]):
        return "confused"
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

        # SAFE CHECK
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
    SearchLog.objects.create( #stores the data )
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
You are NOT a general chatbot.

You ONLY talk about emotions, feelings, and inner thoughts.

If user tries to talk about random topics:
→ Redirect them back to their feelings politely.

User emotion: {emotion}

TASK:
Ask ONE meaningful question to understand WHY they feel this way.

RULES:
- Only 1 question
- No advice
- No motivation
- No long text
- Very natural tone

Example:
"What’s been bothering you lately?"
"""

        stage = "reason"

    # ---------------- STAGE 2 ----------------
    elif stage == "reason":

        system_prompt = f"""
You are a deep emotional support AI.

User emotion: {emotion}

User has shared their situation.

RESPONSE STRUCTURE (STRICT):

1. Acknowledge feeling (1 short line)
2. Give 2 lines of simple motivation
3. Give 1 simple philosophical insight
4. Ask 1 deep thinking question

RULES:
- Simple English
- No complex words
- No long paragraphs
- No generic lines like "stay strong"
- Make user THINK

Example:

"You feel left out, and that hurts.

But this doesn’t define your worth.
Things change when you take small steps.

Sometimes we compare our inside with others’ outside.

Will this matter after some time?"
"""

        stage = "advice"

    # ---------------- STAGE 3 ----------------
    else:

        system_prompt = f"""
You are an emotional AI.

User emotion: {emotion}

RULES:
- Stay focused on feelings only
- If user goes off-topic → bring back to emotions
- Give insight + calm thinking
- Ask max 1 question (optional)
- Keep it short and real

Avoid:
- robotic tone
- generic advice
"""

    messages = [{"role": "system", "content": system_prompt}] + conversation

    output = query(messages)
    if not output:
        return JsonResponse({"reply": "No response from server."})

    if "error" in output:
        print("API ERROR:", output["error"])
        return JsonResponse({"reply": "API error. Check logs."})

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