from flask import Blueprint, render_template, request, url_for, redirect, session, flash, jsonify
from myapp.database import *
from functools import wraps

import pandas as pd
import matplotlib.pyplot as plt
from myapp import socket

import threading
import sounddevice as sd
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent
import queue
import asyncio

views = Blueprint('views', __name__, static_folder='static', template_folder='templates')

# Variable para almacenar el texto transcrito
texto_transcrito = ""
is_transcribing = False
transcription_thread = None
audio_queue = queue.Queue()

class MyEventHandler(TranscriptResultStreamHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ultimo_transcrito = ""

    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        results = transcript_event.transcript.results
        for result in results:
            for alt in result.alternatives:
                self.ultimo_transcrito = alt.transcript

    def get_transcrito(self):
        return self.ultimo_transcrito

def mic_stream():
    def callback(indata, frames, time, status):
        if status:
            print(status, flush=True)
        audio_queue.put(bytes(indata))

    stream = sd.RawInputStream(
        channels=1,
        samplerate=16000,
        callback=callback,
        blocksize=1024 * 2,
        dtype="int16",
    )

    with stream:
        while is_transcribing:
            sd.sleep(100)

async def transcribir_audio():
    global texto_transcrito
    try:
        client = TranscribeStreamingClient(region="us-east-2")
        stream = await client.start_stream_transcription(
            language_code="es-US",
            media_sample_rate_hz=16000,
            media_encoding="pcm"
        )

        handler = MyEventHandler(stream.output_stream)

        async def write_chunks():
            try:
                while is_transcribing:
                    if not audio_queue.empty():
                        chunk = audio_queue.get()
                        await stream.input_stream.send_audio_event(audio_chunk=chunk)
                await stream.input_stream.end_stream()
            except Exception as e:
                print(f"Error in write_chunks: {e}", flush=True)

        await asyncio.gather(write_chunks(), handler.handle_events())
        texto_transcrito = handler.get_transcrito()
    except Exception as e:
        print(f"Error in transcribir_audio: {e}", flush=True)

def start_transcription_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(transcribir_audio())

# Login decorator to ensure user is logged in before accessing certain routes
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("views.login"))
        return f(*args, **kwargs)

    return decorated


# Index route, this route redirects to login/register page
@views.route("/", methods=["GET", "POST"])
def index():
    """
    Redirects to the login/register page.

    Returns:
        Response: Flask response object.
    """
    return redirect(url_for("views.login"))


# Register a new user and hash password
@views.route("/register", methods=["GET", "POST"])
def register():
    """
    Handles user registration and password hashing.

    Returns:
        Response: Flask response object.
    """
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        username = request.form["username"].strip().lower()
        password = request.form["password"]

        # Check if the user already exists
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("User already exists with that username.")
            return redirect(url_for("views.login"))

        # Create a new user
        new_user = User(username=username, email=email, password=password)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        # Create a new chat list for the newly registered user
        new_chat = Chat(user_id=new_user.id, chat_list=[])
        db.session.add(new_chat)
        db.session.commit()

        flash("Registration successful.")
        return redirect(url_for("views.login"))

    return render_template("auth.html")


@views.route("/login", methods=["GET", "POST"])
def login():
    """
    Handles user login and session creation.

    Returns:
        Response: Flask response object.
    """
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        # Query the database for the inputted email address
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            # Create a new session for the newly logged-in user
            session["user"] = {
                "id": user.id,
                "username": user.username,
                "email": user.email,
            }
            return redirect(url_for("views.chat"))
        else:
            flash("Invalid login credentials. Please try again.")
            return redirect(url_for("views.login"))

    return render_template("auth.html")


@views.route("/new-chat", methods=["POST"])
@login_required
def new_chat():
    """
    Creates a new chat room and adds users to the chat list.

    Returns:
        Response: Flask response object.
    """
    user_id = session["user"]["id"]
    new_chat_email = request.form["email"].strip().lower()

    # If user is trying to add themselves, do nothing
    if new_chat_email == session["user"]["email"]:
        return redirect(url_for("views.chat"))

    # Check if the recipient user exists
    recipient_user = User.query.filter_by(email=new_chat_email).first()
    if not recipient_user:
        return redirect(url_for("views.chat"))

    # Check if the chat already exists
    existing_chat = Chat.query.filter_by(user_id=user_id).first()
    """if not existing_chat:
        existing_chat = Chat(user_id=user_id, chat_list=[])
        db.session.add(existing_chat)
        db.session.commit()"""

    # Check if the new chat is already in the chat list
    if recipient_user.id not in [user_chat["user_id"] for user_chat in existing_chat.chat_list]:
        # Generate a room_id (you may use your logic to generate it)
        room_id = str(int(recipient_user.id) + int(user_id))[-4:]

        # Add the new chat to the chat list of the current user
        updated_chat_list = existing_chat.chat_list + [{"user_id": recipient_user.id, "room_id": room_id}]
        existing_chat.chat_list = updated_chat_list

        # Save the changes to the database
        existing_chat.save_to_db()

        # Create a new chat list for the recipient user if it doesn't exist
        recipient_chat = Chat.query.filter_by(user_id=recipient_user.id).first()
        if not recipient_chat:
            recipient_chat = Chat(user_id=recipient_user.id, chat_list=[])
            db.session.add(recipient_chat)
            db.session.commit()

        # Add the new chat to the chat list of the recipient user
        updated_chat_list = recipient_chat.chat_list + [{"user_id": user_id, "room_id": room_id}]
        recipient_chat.chat_list = updated_chat_list
        recipient_chat.save_to_db()

        # Create a new message entry for the chat room
        new_message = Message(room_id=room_id)
        db.session.add(new_message)
        db.session.commit()

    return redirect(url_for("views.chat"))


@views.route("/chat/", methods=["GET", "POST"])
@login_required
def chat():
    """
    Renders the chat interface and displays chat messages.

    Returns:
        Response: Flask response object.
    """
    # Get the room id in the URL or set to None
    room_id = request.args.get("rid", None)

    # Get the chat list for the user
    current_user_id = session["user"]["id"]
    current_user_chats = Chat.query.filter_by(user_id=current_user_id).first()
    chat_list = current_user_chats.chat_list if current_user_chats else []

    # Initialize context that contains information about the chat room
    data = []

    for chat in chat_list:
        # Query the database to get the username of users in a user's chat list
        username = User.query.get(chat["user_id"]).username
        is_active = room_id == chat["room_id"]

        try:
            # Get the Message object for the chat room
            message = Message.query.filter_by(room_id=chat["room_id"]).first()

            # Get the last ChatMessage object in the Message's messages relationship
            last_message = message.messages[-1]

            # Get the message content of the last ChatMessage object
            last_message_content = last_message.content
        except (AttributeError, IndexError):
            # Set variable to this when no messages have been sent to the room
            last_message_content = "This place is empty. No messages ..."

        data.append({
            "username": username,
            "room_id": chat["room_id"],
            "is_active": is_active,
            "last_message": last_message_content,
        })

    # Get all the message history in a certain room
    messages = Message.query.filter_by(room_id=room_id).first().messages if room_id else []

    return render_template(
        "chat.html",
        user_data=session["user"],
        room_id=room_id,
        data=data,
        messages=messages,
    )

#ALGORITMOS PARA LA TRANSCRIPCIÓN
@views.route('/start_transcription', methods=['POST'])
@login_required
def start_transcription():
    global is_transcribing, transcription_thread
    if not is_transcribing:
        is_transcribing = True
        transcription_thread = threading.Thread(target=start_transcription_thread)
        transcription_thread.start()
        mic_stream_thread = threading.Thread(target=mic_stream)
        mic_stream_thread.start()
    return jsonify({'status': 'Transcription started'})

@views.route('/stop_transcription', methods=['POST'])
@login_required
def stop_transcription():
    global is_transcribing, transcription_thread
    if is_transcribing:
        is_transcribing = False
        transcription_thread.join()
    return jsonify({'status': 'Transcription stopped'})

@views.route('/transcribe', methods=['GET'])
@login_required
def transcribe():
    return jsonify({'texto': texto_transcrito})

#ALGORITMOS PARA LA TRADUCCIÓN
@views.route('/start_traduccion', methods=['POST'])
@login_required
def start_traduccion():
    ##LLENAR CON EL ALGORITMO QUE PRENDE LA CÁMARA Y TRADUCE EL LENGUAJE DE SEÑAS
    #LA TRADUCCIÓN ES ALMACENADA COMO TEXTO, PERO NO SE MUESTRA EN EL HTML COMO PARTE DEL CHAT
    return jsonify({'status': 'Traduccion started'})

@views.route('/stop_traduccion', methods=['POST'])
@login_required
def stop_traduccion():
    #DETENER LA TRADUCCIÓN (APAGAR LA CÁMARA)
    #ADQUIRIR LOS DATOS DESDE EL chat.html
    #para el ejemplo de transcripción se usó: const response = await fetch('/transcribe');const data = await response.json();, document.getElementById('messageInput').value = data.texto;
    
    return jsonify({'status': 'Traduccion stopped'})

@views.route('/traduccion', methods=['GET'])
@login_required
def traduccion():
    return jsonify({'texto': texto_transcrito})




# Custom time filter to be used in the jinja template
@views.app_template_filter("ftime")
def ftime(date):
    dt = datetime.fromtimestamp(int(date))
    time_format = "%I:%M %p"  # Use  %I for 12-hour clock format and %p for AM/PM
    formatted_time = dt.strftime(time_format)

    formatted_time += " | " + dt.strftime("%m/%d")
    return formatted_time


@views.route('/visualize')
def visualize():
    """
    TODO: Utilize pandas and matplotlib to analyze the number of users registered to the app.
    Create a chart of the analysis and convert it to base64 encoding for display in the template.

    Returns:
        Response: Flask response object.
    """
    pass


@views.route('/get_name')
def get_name():
    """
    :return: json object with username
    """
    data = {'name': ''}
    if 'username' in session:
        data = {'name': session['username']}

    return jsonify(data)


@views.route('/get_messages')
def get_messages():
    """
    query the database for messages o in a particular room id
    :return: all messages
    """
    pass


@views.route('/leave')
def leave():
    """
    Emits a 'disconnect' event and redirects to the home page.

    Returns:
        Response: Flask response object.
    """
    socket.emit('disconnect')
    return redirect(url_for('views.home'))