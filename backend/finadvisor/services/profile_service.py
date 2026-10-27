from firebase_admin import firestore
from datetime import datetime

def update_financial_data(uid, financial_data):
    """Update financial data for the user in Firestore."""
    try:
        db = firestore.client()
        profile_ref = db.collection('profiles').document(uid)

        # Update financial data
        profile_ref.set({
            "financial_data": financial_data,
            "updatedAt": datetime.utcnow().isoformat()
        }, merge=True)

        return {"status": "success", "message": "Financial data updated"}
    except Exception as e:
        return {"status": "error", "message": "Failed to update financial data"}

def get_financial_data(uid):
    """Retrieve financial data for the user from Firestore."""
    try:
        db = firestore.client()
        profile_ref = db.collection('profiles').document(uid)
        profile = profile_ref.get()
        if profile.exists:
            return {"status": "success", "data": profile.to_dict()}
        else:
            return {"status": "error", "message": "Profile not found"}
    except Exception as e:
        return {"status": "error", "message": "Failed to retrieve financial data"}


def update_user_field(uid, field_name, field_value):
    """
    Update a specific field for the user in Firestore.

    Args:
        uid (str): The user ID.
        field_name (str): The field to update.
        field_value (Any): The value to update the field with.

    Returns:
        dict: Status and message of the operation.
    """
    try:
        db = firestore.client()
        profile_ref = db.collection('profiles').document(uid)

        # Update the specific field
        profile_ref.set({
            field_name: field_value,
            "updatedAt": datetime.utcnow().isoformat()
        }, merge=True)

        return {"status": "success", "message": f"Field '{field_name}' updated successfully"}
    except Exception as e:
        print(f"Error updating field '{field_name}':", str(e))
        return {"status": "error", "message": f"Failed to update field '{field_name}'"}


def get_user_field(uid, field_name=None):
    """
    Retrieve specific field(s) for the user from Firestore.

    Args:
        uid (str): The user ID.
        field_name (str, optional): The specific field to retrieve. If None, retrieves all data.

    Returns:
        dict: Status and data or message.
    """
    try:
        db = firestore.client()
        profile_ref = db.collection('profiles').document(uid)
        profile = profile_ref.get()

        if profile.exists:
            data = profile.to_dict()
            if field_name:
                # Return specific field if it exists
                if field_name in data:
                    return {"status": "success", "data": {field_name: data[field_name]}}
                else:
                    return {"status": "error", "message": f"Field '{field_name}' not found"}
            else:
                # Return all fields if no specific field is requested
                return {"status": "success", "data": data}
        else:
            return {"status": "error", "message": "Profile not found"}
    except Exception as e:
        return {"status": "error", "message": "Failed to retrieve data"}

def save_profile(uid, name, email):
    """Save basic user profile information in Firestore."""
    try:
        db = firestore.client()
        profile_ref = db.collection('profiles').document(uid)

        # Save user profile
        profile_ref.set({
            "name": name,
            "email": email,
            "createdAt": datetime.utcnow().isoformat()
        }, merge=True)

        return {"status": "success", "message": "User profile saved"}
    except Exception as e:
        return {"status": "error", "message": "Failed to save user profile"}
