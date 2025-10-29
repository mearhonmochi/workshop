from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from flask_socketio import SocketIO, join_room, leave_room, emit
import random
import string
import re

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_super_secret_key_here' # ควรเปลี่ยนเป็นคีย์ที่ซับซ้อนกว่านี้
socketio = SocketIO(app, cors_allowed_origins="*") # อนุญาต CORS สำหรับการพัฒนา

# --- Database Mockup (in-memory for simplicity) ---
# ในโปรดักชันควรใช้ Database จริงๆ เช่น MongoDB หรือ Firebase
rooms = {} # {room_code: {room_name, host_sid, status, players: {sid: {name, status, reaction_time}}}}

# --- Helper Functions ---
def generate_room_code():
    """Generates a unique 6-character alphanumeric room code."""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if code not in rooms:
            return code

def validate_player_name(name):
    """Validates player name according to requirements."""
    if not name or not (1 <= len(name) <= 20):
        return False, "ชื่อผู้เล่นต้องมีความยาว 1-20 ตัวอักษร"
    # Regex: อนุญาต A-Z, a-z, ก-ฮ เท่านั้น
    if not re.fullmatch(r'^[A-Za-zก-ฮ]+$', name):
        return False, "ชื่อผู้เล่นต้องเป็นตัวอักษรเท่านั้น (A-Z, ก-ฮ)"
    return True, ""

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create-room', methods=['GET', 'POST'])
def create_room():
    if request.method == 'POST':
        room_name = request.form.get('room_name')
        if not room_name or not (1 <= len(room_name) <= 30):
            return render_template('create_room.html', error="ชื่อห้องต้องมีความยาว 1-30 ตัวอักษร")

        room_code = generate_room_code()
        # session['room_code'] = room_code # Host doesn't need to join room via button click
        # The actual host 'joining' happens via socket connection
        
        rooms[room_code] = {
            'room_name': room_name,
            'host_sid': None, # Will be set when host connects via socket
            'status': 'waiting', # waiting, started, ended
            'players': {}, # {sid: {name, status: waiting/foul/done, reaction_time}}
            'results': [] # [{name, reaction_time}]
        }
        print(f"Room {room_code} created: {room_name}")
        return redirect(url_for('host_room_page', room_code=room_code))
    return render_template('create_room.html')

@app.route('/join-room', methods=['GET', 'POST'])
def join_room_page():
    if request.method == 'POST':
        player_name = request.form.get('player_name')
        room_code = request.form.get('room_code').upper() # Ensure uppercase

        is_valid, msg = validate_player_name(player_name)
        if not is_valid:
            return render_template('join_room.html', error=msg, player_name=player_name, room_code=room_code)
        
        if room_code not in rooms:
            return render_template('join_room.html', error="ไม่พบรหัสห้องนี้", player_name=player_name, room_code=room_code)

        # Check for duplicate name in the specific room
        for player_sid in rooms[room_code]['players']:
            if rooms[room_code]['players'][player_sid]['name'] == player_name:
                return render_template('join_room.html', error="ชื่อผู้เล่นซ้ำในห้องนี้", player_name=player_name, room_code=room_code)

        session['player_name'] = player_name
        session['room_code'] = room_code
        return redirect(url_for('player_game_page', room_code=room_code))
    return render_template('join_room.html')

@app.route('/room/<room_code>')
def host_room_page(room_code):
    if room_code not in rooms:
        return redirect(url_for('index')) # Or show an error
    return render_template('host_room.html', room_code=room_code, room_name=rooms[room_code]['room_name'])

@app.route('/play/<room_code>')
def player_game_page(room_code):
    if room_code not in rooms:
        return redirect(url_for('index')) # Or show an error
    
    # Ensure player has a name in session, otherwise redirect to join
    if 'player_name' not in session or session['room_code'] != room_code:
        return redirect(url_for('join_room_page')) # Force re-join if session missing
        
    return render_template('player_game.html', room_code=room_code, player_name=session['player_name'])


# --- Socket.IO Events ---

@socketio.on('connect')
def handle_connect():
    sid = request.sid
    print(f"Client connected: {sid}")

    # For Host: If host is connecting to their room
    room_code = request.args.get('room_code')
    is_host = request.args.get('is_host') == 'true'

    if is_host and room_code in rooms and rooms[room_code]['host_sid'] is None:
        rooms[room_code]['host_sid'] = sid
        join_room(room_code)
        print(f"Host {sid} joined room {room_code}")
        # Send initial player list to host
        current_players = [{'name': p['name'], 'status': p['status']} for p in rooms[room_code]['players'].values()]
        emit('player_list_update', current_players, room=sid)

    # For Player: If player is connecting to a game room
    elif 'player_name' in session and 'room_code' in session:
        player_name = session['player_name']
        room_code = session['room_code']

        if room_code in rooms:
            # Add player to room data if not already there (e.g., re-connect)
            if sid not in rooms[room_code]['players']:
                rooms[room_code]['players'][sid] = {
                    'name': player_name,
                    'status': 'waiting',
                    'reaction_time': None
                }
            join_room(room_code)
            print(f"Player {player_name} ({sid}) joined room {room_code}")

            # Notify host and other players about new player
            current_players = [{'name': p['name'], 'status': p['status']} for p in rooms[room_code]['players'].values()]
            emit('player_list_update', current_players, room=room_code) # To all in room
            
            # Send initial game status to the connecting player
            emit('game_state_update', {'status': rooms[room_code]['status']}, room=sid)
        else:
            emit('redirect', {'url': url_for('join_room_page')}, room=sid) # Redirect player if room somehow disappeared

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"Client disconnected: {sid}")

    # Check if a host disconnected
    for room_code, room_data in rooms.items():
        if room_data['host_sid'] == sid:
            print(f"Host {sid} disconnected from room {room_code}. Room might become unmanaged.")
            room_data['host_sid'] = None # Mark host as disconnected
            # Optionally, you might want to close the room or transfer host rights
            emit('host_disconnected', {'message': 'Host has disconnected. Waiting for host to reconnect or a new host to take over.'}, room=room_code)
            break
        
        # Check if a player disconnected
        if sid in room_data['players']:
            player_name = room_data['players'][sid]['name']
            del room_data['players'][sid]
            print(f"Player {player_name} ({sid}) left room {room_code}")
            
            # Update player list for everyone in the room
            current_players = [{'name': p['name'], 'status': p['status']} for p in room_data['players'].values()]
            emit('player_list_update', current_players, room=room_code)
            break

# Host controls
@socketio.on('start_game')
def start_game(data):
    room_code = data.get('room_code')
    if room_code in rooms and rooms[room_code]['host_sid'] == request.sid:
        rooms[room_code]['status'] = 'started'
        rooms[room_code]['start_time'] = socketio.server.eio.start_serving_time # Server-side start time
        for player_sid in rooms[room_code]['players']:
            rooms[room_code]['players'][player_sid]['status'] = 'waiting' # Reset player status
            rooms[room_code]['players'][player_sid]['reaction_time'] = None
        rooms[room_code]['results'] = [] # Clear previous results

        emit('game_state_update', {'status': 'started'}, room=room_code)
        print(f"Game started in room {room_code}")
    else:
        emit('error', {'message': 'Unauthorized action or room not found'}, room=request.sid)

@socketio.on('reset_game')
def reset_game(data):
    room_code = data.get('room_code')
    if room_code in rooms and rooms[room_code]['host_sid'] == request.sid:
        rooms[room_code]['status'] = 'waiting'
        for player_sid in rooms[room_code]['players']:
            rooms[room_code]['players'][player_sid]['status'] = 'waiting'
            rooms[room_code]['players'][player_sid]['reaction_time'] = None
        rooms[room_code]['results'] = []

        emit('game_state_update', {'status': 'waiting'}, room=room_code)
        emit('player_list_update', [{'name': p['name'], 'status': p['status']} for p in rooms[room_code]['players'].values()], room=room_code) # Update player list (status reset)
        emit('game_results_update', [], room=room_code) # Clear results for everyone
        print(f"Game reset in room {room_code}")
    else:
        emit('error', {'message': 'Unauthorized action or room not found'}, room=request.sid)

# Player action
@socketio.on('player_buzz')
def player_buzz(data):
    room_code = data.get('room_code')
    player_sid = request.sid

    if room_code in rooms and player_sid in rooms[room_code]['players']:
        room = rooms[room_code]
        player = room['players'][player_sid]

        if room['status'] == 'waiting':
            # Player buzzed before game started (FAUL)
            if player['status'] == 'waiting': # Only set to foul if not already done/fouled
                player['status'] = 'foul'
                emit('player_status_update', {'player_sid': player_sid, 'status': 'foul'}, room=player_sid)
                print(f"Player {player['name']} in {room_code} fouled!")
                # Update host's player list
                current_players = [{'name': p['name'], 'status': p['status']} for p in room['players'].values()]
                emit('player_list_update', current_players, room=room_code)
        elif room['status'] == 'started' and player['status'] == 'waiting':
            # Player buzzed after game started and hasn't buzzed yet
            buzz_time = socketio.server.eio.start_serving_time
            reaction_time = (buzz_time - room['start_time']) / 1000.0 # in seconds

            player['status'] = 'done'
            player['reaction_time'] = reaction_time
            
            # Add to results and sort
            room['results'].append({'name': player['name'], 'reaction_time': reaction_time})
            room['results'].sort(key=lambda x: x['reaction_time']) # Sort by fastest time

            emit('player_status_update', {'player_sid': player_sid, 'status': 'done'}, room=player_sid)
            print(f"Player {player['name']} in {room_code} buzzed in {reaction_time:.3f}s")
            
            # Update host's player list and results
            current_players = [{'name': p['name'], 'status': p['status']} for p in room['players'].values()]
            emit('player_list_update', current_players, room=room_code)
            emit('game_results_update', room['results'], room=room_code)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)