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

# --- Configuration & Helpers ---

def load_tokens(server_name):
    """Loads regional tokens from JSON files."""
    try:
        mapping = {"IND": "token_ind.json", "BR": "token_br.json", "US": "token_br.json"}
        filename = mapping.get(server_name, "token_bd.json")
        with open(filename, "r") as f:
            return json.load(f)
    except Exception:
        return None

def encrypt_message(plaintext):
    """AES-CBC Encryption for Garena handshake."""
    try:
        key, iv = b'Yg&tc%DEuh6%Zc^8', b'6oyZDr22E3ychjM%'
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return binascii.hexlify(cipher.encrypt(pad(plaintext, 16))).decode('utf-8')
    except Exception:
        return None

def enc_uid_query(uid):
    """Encodes UID for the GetPlayerPersonalShow endpoint."""
    msg = uid_generator_pb2.uid_generator()
    msg.saturn_ = int(uid)
    msg.garena = 1
    return encrypt_message(msg.SerializeToString())

# --- Core Logic ---

async def send_batch_likes(uid, server_name, url, tokens):
    """Sends requests in parallel to avoid Vercel timeouts."""
    # Create Like Protobuf
    msg = like_pb2.like()
    msg.uid = int(uid)
    msg.region = server_name
    edata = bytes.fromhex(encrypt_message(msg.SerializeToString()))

    headers_base = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 12; Build/SQ1A.220205.002)",
        'Content-Type': "application/x-www-form-urlencoded",
        'ReleaseVersion': "OB53"
    }

    results = {"success": 0, "failed": 0}
    
    async with aiohttp.ClientSession(headers=headers_base) as session:
        tasks = []
        # Limit to 25 to ensure completion within Vercel's 10s limit
        for t in tokens[:25]:
            auth_headers = {'Authorization': f"Bearer {t['token']}"}
            tasks.append(session.post(url, data=edata, headers=auth_headers, timeout=5))
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        for res in responses:
            if hasattr(res, 'status') and res.status == 200:
                results["success"] += 1
            else:
                results["failed"] += 1
    return results

def make_request(encrypt_hex, server_name, token):
    """Fetches profile data using requests (synchronous)."""
    urls = {
        "IND": "https://client.ind.freefiremobile.com/GetPlayerPersonalShow",
        "BR": "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
    }
    url = urls.get(server_name, "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow")
    
    headers = {
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded",
        'ReleaseVersion': "OB53"
    }
    try:
        resp = requests.post(url, data=bytes.fromhex(encrypt_hex), headers=headers, timeout=8)
        if resp.status_code != 200: return None
        items = like_count_pb2.Info()
        items.ParseFromString(resp.content)
        return items
    except:
        return None

# --- Async Route Handler ---

@app.route('/like', methods=['GET'])
async def handle_requests(): # Note the 'async def'
    uid = request.args.get("uid")
    server_name = request.args.get("server_name", "BD").upper()

    if not uid:
        return jsonify({"error": "UID is required"}), 400

    try:
        # 1. Setup
        tokens = load_tokens(server_name)
        if not tokens: return jsonify({"error": "Tokens not found"}), 500
        
        main_token = tokens[0]['token']
        enc_uid = enc_uid_query(uid)

        # 2. Before Check
        before_data = make_request(enc_uid, server_name, main_token)
        if not before_data:
            return jsonify({"error": "Failed to fetch player profile. Token/Version mismatch."}), 500
        
        before_json = json.loads(MessageToJson(before_data))
        before_likes = int(before_json.get('AccountInfo', {}).get('Likes', 0))

        # 3. Action
        like_urls = {
            "IND": "https://client.ind.freefiremobile.com/LikeProfile",
            "BR": "https://client.us.freefiremobile.com/LikeProfile"
        }
        target_url = like_urls.get(server_name, "https://clientbp.ggpolarbear.com/LikeProfile")
        
        # Await the batch directly instead of using asyncio.run()
        batch_summary = await send_batch_likes(uid, server_name, target_url, tokens)

        # 4. After Check
        after_data = make_request(enc_uid, server_name, main_token)
        after_json = json.loads(MessageToJson(after_data)) if after_data else {}
        after_likes = int(after_json.get('AccountInfo', {}).get('Likes', 0))

        return jsonify({
            "status": 1 if after_likes > before_likes else 2,
            "PlayerNickname": after_json.get('AccountInfo', {}).get('PlayerNickname', 'Unknown'),
            "LikesBefore": before_likes,
            "LikesAfter": after_likes,
            "Gain": after_likes - before_likes,
            "Batch": batch_summary
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run()
