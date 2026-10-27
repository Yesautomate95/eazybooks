from fastapi import APIRouter, Request, HTTPException, Response, Form
from typing import Dict, Any
import json
import os
from ..services.whatsapp_handler import process_whatsapp_message
from twilio.request_validator import RequestValidator

router = APIRouter()

@router.post("/webhook")
async def whatsapp_webhook(request: Request):
    try:
        # Get the request data and headers
        body = await request.form()
        headers = request.headers
        
        # Debug logging
        print("Webhook received:")
        print(f"Headers: {dict(headers)}")
        print(f"Body: {dict(body)}")
        
        # Validate the request is from Twilio
        validator = RequestValidator(os.getenv('TWILIO_AUTH_TOKEN'))
        url = str(request.url)
        signature = headers.get('X-Twilio-Signature', '')
        
        # Convert form data to dict
        form_data = dict(body)
        
        # Debug logging
        print(f"URL for validation: {url}")
        print(f"Signature: {signature}")
        print(f"Form data: {form_data}")
        
        if not validator.validate(url, form_data, signature):
            print("Signature validation failed!")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")
            
        # Process the message
        result = await process_whatsapp_message(form_data)
        print(f"Message processed with result: {result}")
        return result
    except Exception as e:
        error_msg = f"Error processing webhook: {str(e)}"
        print(error_msg)
        print(f"Full error: {e.__class__.__name__}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/webhook")
async def verify_webhook(request: Request):
    # Twilio doesn't require webhook verification like Meta
    # This endpoint can be used for health checks
    return {"status": "healthy"} 