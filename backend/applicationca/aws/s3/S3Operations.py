import io
import json
import mimetypes

import aioboto3
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
from fastapi import UploadFile

from applicationca.aws.constants.AwsConstants import USER_DATA_S3_BUCKET_NAME, FILE_VIEW_URL
from applicationca.database.database_retrieval import get_file_content

# Initialize AWS S3 client (uses IAM role if running on EC2)
s3 = boto3.client("s3")


async def upload_to_s3(file: UploadFile, folder_id: str, user_id: str):
    file_content = await file.read()
    file_key = f"{user_id}/{folder_id}/{file.filename}"

    mime_type, _ = mimetypes.guess_type(file.filename)
    if not mime_type:
        mime_type = "application/octet-stream"

    session = aioboto3.Session()
    async with session.client("s3") as s3_client:
        await s3_client.put_object(
            Bucket=USER_DATA_S3_BUCKET_NAME,
            Key=file_key,
            Body=file_content,
            ContentType=mime_type,
            # Use inline so the user can view it in the browser if supported,
            # but can still download it using the browser's download controls.
            ContentDisposition=f'inline; filename="{file.filename}"'
        )

        return {
            "s3_key": file_key,
            "s3_url": f"{FILE_VIEW_URL}/{file_key}",
            "file_content": file_content
        }

def upload_file_to_s3(file, user_id: str, s3_bucket: str):
    try:
        # Keep the original filename
        original_filename = file.filename

        # Define S3 object key (path in bucket)
        object_key = f"{user_id}/{original_filename}"

        # Make a copy of the file content before uploading
        file.file.seek(0)
        file_content = file.file.read()

        # Create a new BytesIO object for uploading
        file_stream = io.BytesIO(file_content)

        # Upload file to S3
        s3.upload_fileobj(file_stream, s3_bucket, object_key)

        # Generate public S3 file URL
        file_url = f"https://{s3_bucket}.s3.amazonaws.com/{object_key}"

        return {
            "file_url": file_url,
            "object_key": object_key,
            "content": file_content
        }

    except (NoCredentialsError, PartialCredentialsError):
        raise Exception("AWS credentials not available. Ensure EC2 IAM role is set up.")

    except Exception as e:
        raise Exception(f"Failed to upload file: {str(e)}")


async def generate_presigned_url(user_id: str, filename: str, folder_id: str, expiration_seconds: int = 900):
    try:
        object_key = f"{user_id}/{folder_id}/{filename}"
        return FILE_VIEW_URL + "/" + object_key

    except Exception as e:
        raise Exception(f"Error generating pre-signed URL: {str(e)}")


async def retrieve_full_document(user_id: str, filename: str, folder_id: str):
    try:
        print("retrieve_full_document ")
        print(f"{user_id} {filename} {folder_id}")
        content = json.dumps(get_file_content(user_id, filename, folder_id))
        return content
    except Exception as e:
        raise Exception(f"No Document found: {str(e)}")
def get_all_files_data(user_id: str, s3_bucket:str):

    try:
        prefix = f"{user_id}/"  # Folder path in S3
        response = s3.list_objects_v2(Bucket=s3_bucket, Prefix=prefix)

        if "Contents" not in response:
            return {}  # No files found

        files_data = {}

        # Iterate through each file in the folder
        for obj in response["Contents"]:
            file_key = obj["Key"]
            file_name = file_key.split("/")[-1]  # Extract filename from path

            # Read file content from S3
            file_stream = io.BytesIO()
            s3.download_fileobj(s3_bucket, file_key, file_stream)
            file_stream.seek(0)  # Reset stream position

            files_data[file_name] = file_stream.read()  # Store file content as binary

        return files_data

    except (NoCredentialsError, PartialCredentialsError):
        raise Exception("AWS credentials not available. Ensure EC2 IAM role is set up.")

    except Exception as e:
        raise Exception(f"Failed to retrieve files: {str(e)}")

def get_file_data(user_id: str, file_name: str, s3_bucket:str):

    try:
        file_key = f"{user_id}/{file_name}"  # Full S3 key (folder + file name)

        # Read file content from S3
        file_stream = io.BytesIO()
        s3.download_fileobj(s3_bucket, file_key, file_stream)
        file_stream.seek(0)  # Reset stream position

        return file_stream.read()  # Return file content as binary data

    except (NoCredentialsError, PartialCredentialsError):
        raise Exception("AWS credentials not available. Ensure EC2 IAM role is set up.")

    except Exception as e:
        raise Exception(f"Failed to retrieve file: {str(e)}")

async def delete_file_from_s3(user_id: str, file_name: str, folder_id: str):
    """
    Delete a file from S3 bucket.
    
    Args:
        user_id (str): The ID of the user who owns the file
        file_name (str): The name of the file to delete
        folder_id (str): The ID of the folder (locker or client) containing the file
        
    Returns:
        bool: True if deletion was successful, False otherwise
        
    Raises:
        Exception: If there's an error during deletion
    """
    try:
        # Construct the S3 key (path) for the file
        file_key = f"{user_id}/{folder_id}/{file_name}"
        
        # Initialize S3 client
        session = aioboto3.Session()
        async with session.client("s3") as s3_client:
            # Delete the object from S3
            await s3_client.delete_object(
                Bucket=USER_DATA_S3_BUCKET_NAME,
                Key=file_key
            )
            
            return True
            
    except Exception as e:
        raise Exception(f"Failed to delete file from S3: {str(e)}")