import asyncio
import os
from dotenv import load_dotenv
 
load_dotenv()  # reads your .env with the R2 credentials
 
from integrations.r2 import R2Client
 
 
async def main():
    client = R2Client(
        account_id=os.environ["R2_ACCOUNT_ID"],
        access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        bucket=os.environ["R2_BUCKET"],
        public_base_url=os.environ["R2_PUBLIC_BASE_URL"],
    )
 
    with open("test.jpg", "rb") as f:
        data = f.read()
 
    print(f"Uploading {len(data)} bytes to R2...")
    url = await client.upload(data, key="probe-test.jpg", content_type="image/jpeg")
    print(f"SUCCESS. Public URL: {url}")
    print("Now open that URL in your browser to confirm the image loads.")
 
    await client.aclose()
 
 
asyncio.run(main())