from flask import Flask, request, jsonify
import asyncio
import json
import binascii
import aiohttp
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToJson
from google.protobuf.message import DecodeError
import like_pb2
import like_count_pb2
import uid_generator_pb2

app = Flask(__name__)

# --- Helper Functions ---

def load_tokens(server_name):
    filename = "token_bd.json" # ডিফল্ট BD সার্ভার
    if server_name == "IND":
        filename = "token_ind.json"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        filename = "token_br.json"
    
    try:
        with open(filename, "r") as f:
            tokens = json.load(f)
            return tokens
    except Exception as e:
        app.logger.error(f"Error loading tokens: {e}")
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
        return None

def create_like_protobuf(user_id, region):
    message = like_pb2.like()
    message.uid = int(user_id)
    message.region = region
    return message.SerializeToString()

def create_info_protobuf(uid):
    message = uid_generator_pb2.uid_generator()
    message.saturn_ = int(uid)
    message.garena = 1
    return message.SerializeToString()

# --- Async Request Handling ---

async def send_single_like(encrypted_uid, token, url):
    """একটি নির্দিষ্ট টোকেন দিয়ে লাইক পাঠায়"""
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded",
        'X-Unity-Version': "2018.4.11f1",
        'ReleaseVersion': "OB53"
    }
    edata = bytes.fromhex(encrypted_uid)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=edata, headers=headers, timeout=10) as response:
                return response.status
    except:
        return None

async def send_batch_likes(uid, server_name, url, tokens):
    """ব্যাচ আকারে এবং বিরতি দিয়ে লাইক পাঠায়"""
    region = server_name
    proto = create_like_protobuf(uid, region)
    enc_uid = encrypt_message(proto)
    
    results = []
    # ১০টি করে রিকোয়েস্ট একসাথে পাঠানো হবে (Batching)
    batch_size = 10 
    
    for i in range(0, len(tokens), batch_size):
        batch = tokens[i : i + batch_size]
        tasks = []
        for t_info in batch:
            tasks.append(send_single_like(enc_uid, t_info['token'], url))
        
        # ব্যাচ রান করা
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        
        # সার্ভার ব্লক এড়াতে ০.৫ সেকেন্ড বিরতি
        await asyncio.sleep(0.5) 
    
    return results

# --- Player Info Fetcher ---

def get_player_data(uid, server_name, token):
    """লাইক চেক করার জন্য প্লেয়ার প্রোফাইল তথ্য আনে"""
    if server_name == "IND":
        url = "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        url = "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
    else:
        url = "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"

    proto = create_info_protobuf(uid)
    enc_uid = encrypt_message(proto)
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded"
    }
    
    try:
        resp = requests.post(url, data=bytes.fromhex(enc_uid), headers=headers, verify=False, timeout=10)
        items = like_count_pb2.Info()
        items.ParseFromString(resp.content)
        return items
    except:
        return None

# --- Main Route ---

@app.route('/like', methods=['GET'])
def handle_like():
    uid = request.args.get("uid")
    server_name = request.args.get("server_name", "BD").upper()

    if not uid:
        return jsonify({"error": "UID required"}), 400

    # ১. টোকেন লোড করা
    tokens = load_tokens(server_name)
    if not tokens:
        return jsonify({"error": "No tokens found for this server"}), 500

    try:
        # ২. লাইক পাঠানোর আগের অবস্থা দেখা
        first_token = tokens[0]['token']
        before_data = get_player_data(uid, server_name, first_token)
        before_likes = getattr(before_data.AccountInfo, 'Likes', 0) if before_data else 0

        # ৩. লাইক পাঠানোর URL নির্ধারণ
        if server_name == "IND":
            like_url = "https://client.ind.freefiremobile.com/LikeProfile"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            like_url = "https://client.us.freefiremobile.com/LikeProfile"
        else:
            like_url = "https://clientbp.ggpolarbear.com/LikeProfile"

        # ৪. লাইক পাঠানো (Async)
        asyncio.run(send_batch_likes(uid, server_name, like_url, tokens))

        # ৫. লাইক পাঠানোর পরের অবস্থা দেখা
        after_data = get_player_data(uid, server_name, first_token)
        after_likes = getattr(after_data.AccountInfo, 'Likes', 0) if after_data else before_likes
        player_name = getattr(after_data.AccountInfo, 'PlayerNickname', 'Unknown') if after_data else 'Unknown'

        return jsonify({
            "status": "Success",
            "PlayerNickname": player_name,
            "UID": uid,
            "Before_Likes": before_likes,
            "After_Likes": after_likes,
            "Likes_Given": after_likes - before_likes,
            "Total_Tokens_Used": len(tokens)
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
