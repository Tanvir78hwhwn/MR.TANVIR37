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

app = Flask(__name__)

# --- CONFIGURATION ---
# Ensure these match the current OB53 patch requirements
HEADERS = {
    'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 12; ASUS_Z01QD Build/SQ1A.220205.002)",
    'X-Unity-Version': "2018.4.11f1",
    'ReleaseVersion': "OB53",
    'Content-Type': "application/x-www-form-urlencoded",
    'Connection': "keep-alive"
}

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV = b'6oyZDr22E3ychjM%'

# --- UTILITIES ---

def encrypt_payload(data):
    """Encrypts protobuf binary data using AES-CBC."""
    try:
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        padded = pad(data, AES.block_size)
        return binascii.hexlify(cipher.encrypt(padded)).decode('utf-8')
    except Exception as e:
        app.logger.error(f"Encryption Error: {e}")
        return None

def get_server_url(region, endpoint="GetPlayerPersonalShow"):
    """Maps region to the correct Garena cluster URL."""
    if region == "IND":
        base = "https://client.ind.freefiremobile.com"
    elif region in {"BR", "US", "SAC", "NA"}:
        base = "https://client.us.freefiremobile.com"
    else:
        base = "https://clientbp.ggpolarbear.com"
    return f"{base}/{endpoint}"

# --- CORE LOGIC ---

def make_request(uid, server_name, token):
    """Fetches player info with robust error checking."""
    try:
        # Create Protobuf for UID query
        msg = uid_generator_pb2.uid_generator()
        msg.saturn_ = int(uid)
        msg.garena = 1
        
        encrypted_hex = encrypt_payload(msg.SerializeToString())
        url = get_server_url(server_name)
        
        headers = HEADERS.copy()
        headers['Authorization'] = f"Bearer {token}"
        
        response = requests.post(url, data=bytes.fromhex(encrypted_hex), headers=headers, timeout=10)
        
        if response.status_code != 200:
            app.logger.error(f"Garena API Error {response.status_code}: {response.text}")
            return None

        # Decode Protobuf
        info = like_count_pb2.Info()
        info.ParseFromString(response.content)
        return info
    except Exception as e:
        app.logger.error(f"Request Exception: {e}")
        return None

async def send_likes_async(uid, server_name, tokens):
    """Sends high-speed asynchronous like requests."""
    url = get_server_url(server_name, "LikeProfile")
    
    # Create Like Protobuf
    like_msg = like_pb2.like()
    like_msg.uid = int(uid)
    like_msg.region = server_name
    encrypted_hex = encrypt_payload(like_msg.SerializeToString())
    payload = bytes.fromhex(encrypted_hex)

    results = {"success": 0, "failed": 0}
    
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        tasks = []
        for t_data in tokens[:30]: # Limit to 30 tokens for stability
            token = t_data['token']
            auth_headers = {"Authorization": f"Bearer {token}"}
            tasks.append(session.post(url, data=payload, headers=auth_headers, timeout=5))
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        for res in responses:
            if hasattr(res, 'status') and res.status == 200:
                results["success"] += 1
            else:
                results["failed"] += 1
    return results

# --- ROUTES ---

@app.route('/like', methods=['GET'])
def handle_like():
    uid = request.args.get("uid")
    server = request.args.get("server_name", "BD").upper()

    if not uid:
        return jsonify({"error": "UID is required"}), 400

    try:
        # 1. Load Tokens
        token_file = f"token_{server.lower()}.json" if server != "IND" else "token_ind.json"
        try:
            with open(token_file, "r") as f:
                tokens = json.load(f)
        except:
            return jsonify({"error": f"Token file {token_file} missing"}), 500

        main_token = tokens[0]['token']

        # 2. Get Stats BEFORE
        before_data = make_request(uid, server, main_token)
        if not before_data:
            return jsonify({"error": "Failed to fetch profile. Check tokens/version."}), 500
        
        before_json = json.loads(MessageToJson(before_data))
        before_likes = int(before_json.get('AccountInfo', {}).get('Likes', 0))

        # 3. Send Likes
        batch_results = asyncio.run(send_likes_async(uid, server, tokens))

        # 4. Get Stats AFTER
        after_data = make_request(uid, server, main_token)
        after_json = json.loads(MessageToJson(after_data)) if after_data else {}
        after_likes = int(after_json.get('AccountInfo', {}).get('Likes', 0))

        return jsonify({
            "status": 1 if (after_likes > before_likes) else 2,
            "UID": uid,
            "PlayerNickname": after_json.get('AccountInfo', {}).get('PlayerNickname', 'Unknown'),
            "LikesBefore": before_likes,
            "LikesAfter": after_likes,
            "ActualGain": after_likes - before_likes,
            "BatchSummary": batch_results
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(port=5000)
