from flask import Flask, request, jsonify
import asyncio
import random
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToJson
import binascii
import aiohttp
import requests
import json
import like_pb2
import like_count_pb2
import uid_generator_pb2
from google.protobuf.message import DecodeError

app = Flask(__name__)

# --- Utility Functions ---

def load_tokens(server_name):
    try:
        if server_name == "IND":
            filename = "token_ind.json"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            filename = "token_br.json"
        else:
            filename = "token_bd.json"
        
        with open(filename, "r") as f:
            return json.load(f)
    except Exception as e:
        app.logger.error(f"Error loading tokens for server {server_name}: {e}")
        return None

def encrypt_message(plaintext):
    try:
        key = b'Yg&tc%DEuh6%Zc^8'
        iv = b'6oyZDr22E3ychjM%'
        cipher = AES.new(key, AES.MODE_CBC, iv)
        padded_message = pad(plaintext, AES.block_size)
        encrypted_message = cipher.encrypt(padded_message)
        return binascii.hexlify(encrypted_message).decode('utf-8')
    except Exception as e:
        app.logger.error(f"Error encrypting message: {e}")
        return None

def create_protobuf_message(user_id, region):
    try:
        message = like_pb2.like()
        message.uid = int(user_id)
        message.region = region
        return message.SerializeToString()
    except Exception as e:
        app.logger.error(f"Error creating protobuf message: {e}")
        return None

def create_protobuf(uid):
    try:
        message = uid_generator_pb2.uid_generator()
        message.saturn_ = int(uid)
        message.garena = 1
        return message.SerializeToString()
    except Exception as e:
        app.logger.error(f"Error creating uid protobuf: {e}")
        return None

def enc(uid):
    protobuf_data = create_protobuf(uid)
    return encrypt_message(protobuf_data) if protobuf_data else None

# --- Optimized Request Logic ---

async def send_single_like(session, encrypted_uid, token, url):
    """Sends a single request using an existing session."""
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Connection': "Keep-Alive",
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded",
        'X-Unity-Version': "2018.4.11f1",
        'ReleaseVersion': "OB53"
    }
    try:
        edata = bytes.fromhex(encrypted_uid)
        async with session.post(url, data=edata, headers=headers, timeout=10) as response:
            return response.status
    except Exception:
        return 500

async def send_multiple_requests(uid, server_name, url):
    """Sequentially sends requests with delays and success tracking."""
    try:
        protobuf_message = create_protobuf_message(uid, server_name)
        encrypted_uid = encrypt_message(protobuf_message)
        tokens = load_tokens(server_name)

        if not encrypted_uid or not tokens:
            return {"success": 0, "failed": 0, "total": 0}

        # Use each token only once and randomize order
        token_list = [t["token"] for t in tokens]
        random.shuffle(token_list)

        success = 0
        failed = 0
        total = 0
        limit = 35 # Stop early threshold

        async with aiohttp.ClientSession() as session:
            for token in token_list:
                if success >= limit:
                    break
                
                status = await send_single_like(session, encrypted_uid, token, url)
                total += 1
                
                if status == 200:
                    success += 1
                else:
                    failed += 1
                
                # Delay between 0.3 to 0.5 seconds to avoid spam detection
                await asyncio.sleep(random.uniform(0.3, 0.5))

        return {"success": success, "failed": failed, "total": total}
    except Exception as e:
        app.logger.error(f"Error in send_multiple_requests: {e}")
        return {"success": 0, "failed": 0, "total": 0}

# --- Player Info Fetching ---

def decode_protobuf(binary):
    try:
        items = like_count_pb2.Info()
        items.ParseFromString(binary)
        return items
    except Exception as e:
        app.logger.error(f"Protobuf decoding error: {e}")
        return None

def make_request(encrypt, server_name, token):
    try:
        if server_name == "IND":
            url = "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            url = "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
        else:
            url = "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"

        headers = {
            'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
            'Authorization': f"Bearer {token}",
            'Content-Type': "application/x-www-form-urlencoded",
            'ReleaseVersion': "OB53"
        }
        response = requests.post(url, data=bytes.fromhex(encrypt), headers=headers, verify=False)
        return decode_protobuf(response.content)
    except Exception as e:
        app.logger.error(f"Error in make_request: {e}")
        return None

def fetch_player_info(uid):
    try:
        url = f"https://sheihk-anamul-info-ob53.vercel.app/player-info?uid={uid}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            acc = data.get("AccountInfo", {})
            return {
                "Level": acc.get("AccountLevel", "NA"),
                "Region": acc.get("AccountRegion", "NA"),
                "ReleaseVersion": acc.get("ReleaseVersion", "NA")
            }
        return {"Level": "NA", "Region": "NA", "ReleaseVersion": "NA"}
    except Exception:
        return {"Level": "NA", "Region": "NA", "ReleaseVersion": "NA"}

# --- Routes ---

@app.route('/like', methods=['GET'])
def handle_requests():
    uid = request.args.get("uid")
    server_name = request.args.get("server_name", "").upper()
    
    if not uid or not server_name:
        return jsonify({"error": "UID and server_name are required"}), 400

    try:
        # 1. Fetch initial player info
        player_info = fetch_player_info(uid)
        region = player_info["Region"]
        
        # Validate region
        server_to_use = region if (region != "NA" and server_name != region) else server_name
        
        tokens = load_tokens(server_to_use)
        if not tokens:
            return jsonify({"error": "No tokens found for this region"}), 500
        
        main_token = tokens[0]['token']
        encrypted_uid = enc(uid)

        # 2. Get Like count BEFORE
        before_data = make_request(encrypted_uid, server_to_use, main_token)
        if not before_data:
            return jsonify({"error": "Failed to fetch player profile"}), 500
        
        before_json = json.loads(MessageToJson(before_data))
        before_likes = int(before_json.get('AccountInfo', {}).get('Likes', 0))

        # 3. Determine Like endpoint
        if server_to_use == "IND":
            like_url = "https://client.ind.freefiremobile.com/LikeProfile"
        elif server_to_use in {"BR", "US", "SAC", "NA"}:
            like_url = "https://client.us.freefiremobile.com/LikeProfile"
        else:
            like_url = "https://clientbp.ggpolarbear.com/LikeProfile"

        # 4. Trigger optimized sequential requests
        batch_summary = asyncio.run(send_multiple_requests(uid, server_to_use, like_url))

        # 5. Get Like count AFTER
        after_data = make_request(encrypted_uid, server_to_use, main_token)
        after_json = json.loads(MessageToJson(after_data)) if after_data else {}
        
        after_likes = int(after_json.get('AccountInfo', {}).get('Likes', 0))
        actual_gain = after_likes - before_likes

        return jsonify({
            "LikesGivenByAPI": actual_gain,
            "LikesbeforeCommand": before_likes,
            "LikesafterCommand": after_likes,
            "PlayerNickname": after_json.get('AccountInfo', {}).get('PlayerNickname', 'Unknown'),
            "Region": region,
            "Level": player_info["Level"],
            "UID": int(uid),
            "BatchSummary": batch_summary,
            "status": 1 if actual_gain > 0 else 2
        })

    except Exception as e:
        app.logger.error(f"Global error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)
    