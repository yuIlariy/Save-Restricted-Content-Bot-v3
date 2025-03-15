# Copyright (c) 2025 devgagan : https://github.com/devgaganin.  
# Licensed under the GNU General Public License v3.0.  
# See LICENSE file in the repository root for full license text.

import concurrent.futures
import time
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError
import asyncio
import os, re
import cv2
import logging
from datetime import datetime, timedelta

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

PUBLIC_LINK_PATTERN = re.compile(r'(https?://)?(t\.me|telegram\.me)/([^/]+)(/(\d+))?')
PRIVATE_LINK_PATTERN = re.compile(r'(https?://)?(t\.me|telegram\.me)/c/(\d+)(/(\d+))?')

# MongoDB connection
MONGO_URI = os.getenv("MONGO_DB", "mongodb+srv://ggn:surabhimusicbot@ggnvv.g6qusje.mongodb.net/?retryWrites=true&w=majority&appName=ggnvv")
DB_NAME = os.getenv("DB_NAME", "telegram_downloader")


# Initialize MongoDB client
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client[DB_NAME]
users_collection = db["users"]
premium_users_collection = db["premium_users"]
statistics_collection = db["statistics"]
codedb = db["redeem_code"]


def is_private_link(link):
    return bool(PRIVATE_LINK_PATTERN.match(link))

def thumbnail(sender):
    return f'{sender}.jpg' if os.path.exists(f'{sender}.jpg') else None

def hhmmss(seconds):
    return time.strftime('%H:%M:%S', time.gmtime(seconds))

# Helper function to get display name
def get_display_name(user):
    """Get the display name for a user"""
    if user.first_name and user.last_name:
        return f"{user.first_name} {user.last_name}"
    elif user.first_name:
        return user.first_name
    elif user.last_name:
        return user.last_name
    elif user.username:
        return user.username
    else:
        return "Unknown User"

VIDEO_EXTENSIONS = {
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "mpeg", "mpg", "3gp"
}


# Helper functions to interact with the MongoDB collection
async def save_user_data(user_id, key, value):
    """Save user data to MongoDB."""
    await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {key: value}},
        upsert=True
    )
    print(users_collection)

# custom caption and replacement

async def process_text_with_rules(user_id, text):
    """Process text by applying replacement and deletion rules from MongoDB"""
    if not text:
        return ""
    
    try:
        # Get replacement words from MongoDB
        replacements = await get_user_data_key(user_id, "replacement_words", {})
        
        # Get words to delete from MongoDB
        delete_words = await get_user_data_key(user_id, "delete_words", [])
        
        # Apply replacements
        processed_text = text
        for word, replacement in replacements.items():
            processed_text = processed_text.replace(word, replacement)
        
        # Apply deletions (split by words, filter, rejoin)
        if delete_words:
            words = processed_text.split()
            filtered_words = [w for w in words if w not in delete_words]
            processed_text = " ".join(filtered_words)
        
        return processed_text
    except Exception as e:
        logger.error(f"Error processing text with rules: {e}")
        return text  # Return original text if there's an error

async def get_user_data_key(user_id, key, default=None):
    """Get user data from MongoDB."""
    user_data = await users_collection.find_one({"user_id": int(user_id)})  # Ensure user_id is an int
    print(f"Fetching key '{key}' for user {user_id}: {user_data}")  # Debugging
    return user_data.get(key, default) if user_data else default

        

async def screenshot(video: str, duration: int, sender: str) -> str | None:
    """Takes a screenshot from the middle of a video using ffmpeg."""
    
    existing_screenshot = f"{sender}.jpg"
    if os.path.exists(existing_screenshot):
        return existing_screenshot

    time_stamp = hhmmss(duration // 2)  # Midpoint time in HH:MM:SS format
    output_file = datetime.now().isoformat("_", "seconds") + ".jpg"

    cmd = [
        "ffmpeg",
        "-ss", time_stamp,
        "-i", video,
        "-frames:v", "1",
        output_file,
        "-y"
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout, stderr = await process.communicate()

    if os.path.isfile(output_file):
        return output_file
    else:
        print(f"FFmpeg Error: {stderr.decode().strip()}")
        return None
    
async def is_private_chat(event):
    """Filter function to check if the message is from a private chat."""
    return event.is_private

async def get_video_metadata(file_path):
    """Asynchronously extract video metadata"""
    default_values = {'width': 1, 'height': 1, 'duration': 1}
    
    # Run the video processing in a thread pool to avoid blocking
    loop = asyncio.get_event_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    
    try:
        def _extract_metadata():
            try:
                vcap = cv2.VideoCapture(file_path)
                if not vcap.isOpened():
                    return default_values

                width = round(vcap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = round(vcap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = vcap.get(cv2.CAP_PROP_FPS)
                frame_count = vcap.get(cv2.CAP_PROP_FRAME_COUNT)

                if fps <= 0:
                    return default_values

                duration = round(frame_count / fps)
                if duration <= 0:
                    return default_values

                vcap.release()
                return {'width': width, 'height': height, 'duration': duration}
            except Exception as e:
                logger.error(f"Error in video_metadata: {e}")
                return default_values
        
        # Run the CPU-intensive task in a thread pool
        return await loop.run_in_executor(executor, _extract_metadata)
        
    except Exception as e:
        logger.error(f"Error in get_video_metadata: {e}")
        return default_values


async def remove_user_session(user_id):
    """Remove user session string from MongoDB"""
    try:
        await users_collection.update_one(
            {"user_id": user_id},
            {"$unset": {"session_string": ""}}
        )
        logger.info(f"Removed session for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error removing session for user {user_id}: {e}")
        return False



def sanitize_filename(filename):
    """Remove invalid characters from filenames."""
    return re.sub(r'[<>:"/\\|?*]', '_', filename)  # Replace invalid characters with `_`

def get_dummy_filename(info):
    """Generate a dummy filename based on file type if name is missing."""
    file_type = info.get("type", "file")  # Get type or default to 'file'
    extension = {
        "video": "mp4",
        "photo": "jpg",
        "document": "pdf",
        "audio": "mp3"
    }.get(file_type, "bin")  # Default to .bin for unknown types
    
    return f"downloaded_file_{int(time.time())}.{extension}"  # Unique filename

async def save_user_session(user_id, session_string):
    """Save user session string to MongoDB"""
    try:
        # Update or insert user document
        await users_collection.update_one(
            {"user_id": user_id},
            {"$set": {
                "session_string": session_string,
                "updated_at": datetime.now()
            }},
            upsert=True
        )
        logger.info(f"Saved session for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error saving session for user {user_id}: {e}")
        return False


async def get_user_data(user_id):
    """Get user data from MongoDB"""
    try:
        user_data = await users_collection.find_one({"user_id": user_id})
        return user_data
    except Exception as e:
        logger.error(f"Error retrieving user data for {user_id}: {e}")
        return None

# Add after get_user_data() function around line ~225
async def add_premium_user(user_id, duration_value, duration_unit):
    """Add a user as premium member with expiration time"""
    try:
        # Calculate expiration time based on duration
        now = datetime.now()
        expiry_date = None
        
        if duration_unit == "min":
            expiry_date = now + timedelta(minutes=duration_value)
        elif duration_unit == "hours":
            expiry_date = now + timedelta(hours=duration_value)
        elif duration_unit == "days":
            expiry_date = now + timedelta(days=duration_value)
        elif duration_unit == "weeks":
            expiry_date = now + timedelta(weeks=duration_value)
        elif duration_unit == "month":
            expiry_date = now + timedelta(days=30 * duration_value)
        elif duration_unit == "year":
            expiry_date = now + timedelta(days=365 * duration_value)
        elif duration_unit == "decades":
            expiry_date = now + timedelta(days=3650 * duration_value)
        else:
            return False, "Invalid duration unit"
            
        # Add user to premium collection with TTL index
        await premium_users_collection.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "subscription_start": now,
                "subscription_end": expiry_date,
                "expireAt": expiry_date  # TTL field
            }},
            upsert=True
        )
        
        # Ensure TTL index exists (this only creates if it doesn't exist)
        await premium_users_collection.create_index("expireAt", expireAfterSeconds=0)
        
        return True, expiry_date
    except Exception as e:
        logger.error(f"Error adding premium user {user_id}: {e}")
        return False, str(e)

async def is_premium_user(user_id):
    """Check if user is a premium member"""
    try:
        user = await premium_users_collection.find_one({"user_id": user_id})
        if user and "subscription_end" in user:
            # Check if subscription is still valid
            now = datetime.now()
            return now < user["subscription_end"]
        return False
    except Exception as e:
        logger.error(f"Error checking premium status for {user_id}: {e}")
        return False

async def get_premium_details(user_id):
    """Get premium subscription details for a user"""
    try:
        user = await premium_users_collection.find_one({"user_id": user_id})
        if user and "subscription_end" in user:
            return user
        return None
    except Exception as e:
        logger.error(f"Error getting premium details for {user_id}: {e}")
        return None
    
    
a1 = "c2F2ZV9yZXN0cmljdGVkX2NvbnRlbnRfYm90cw==" 
a2 = "Nzk2"
a3 = "Z2V0X21lc3NhZ2Vz" 
a4 = "cmVwbHlfcGhvdG8=" 
a5 = "c3RhcnQ="
attr1 = "cGhvdG8="
attr2 = "ZmlsZV9pZA=="
a7 = "SGkg8J+RiyBXZWxjb21lLCBXYW5uYSBpbnRyby4uLj8gCgrinLPvuI8gSSBjYW4gc2F2ZSBwb3N0cyBmcm9tIGNoYW5uZWxzIG9yIGdyb3VwcyB3aGVyZSBmb3J3YXJkaW5nIGlzIG9mZi4gSSBjYW4gZG93bmxvYWQgdmlkZW9zL2F1ZGlvIGZyb20gWVQsIElOU1RBLCAuLi4gc29jaWFsIHBsYXRmb3JtcwrinLPvuI8gU2ltcGx5IHNlbmQgdGhlIHBvc3QgbGluayBvZiBhIHB1YmxpYyBjaGFubmVsLiBGb3IgcHJpdmF0ZSBjaGFubmVscywgZG8gL2xvZ2luLiBTZW5kIC9oZWxwIHRvIGtub3cgbW9yZS4="
a8 = "Sm9pbiBDaGFubmVs"
a9 = "R2V0IFByZW1pdW0=" 
a10 = "aHR0cHM6Ly90Lm1lL3RlYW1fc3B5X3Bybw==" 
a11 = "aHR0cHM6Ly90Lm1lL2tpbmdvZnBhdGFs" 
