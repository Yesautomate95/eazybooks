from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import RedirectResponse
from typing import Dict
import boto3
from botocore.exceptions import ClientError
import yaml
import logging
from utils.auth_utils import verify_jwt

# Initialize router
router = APIRouter()
logger = logging.getLogger(__name__)

# JWT Authentication Scheme
security = HTTPBearer()

# Initialize S3 client
s3_client = boto3.client('s3', region_name='ap-south-1')

@router.get("/desktop/version")
async def get_desktop_version(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict:
    """
    Get the latest desktop app version from S3.
    Requires JWT authentication.
    Returns:
        Dict: Version information including presigned download URL
    """
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            raise HTTPException(status_code=401, detail="Invalid token")

        # Get the latest.yml file from S3
        response = s3_client.get_object(
            Bucket='aureus-ca-application',
            Key='latest.yml'
        )

        # Read and parse the YAML content
        yaml_content = response['Body'].read().decode('utf-8')
        version_info = yaml.safe_load(yaml_content)
        version = version_info.get('version', '1.0.0')

        # Generate a presigned URL (valid for 1 hour)
        s3_key = f"Aureus CA Setup {version}.exe"
        download_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': 'aureus-ca-application', 'Key': s3_key},
            ExpiresIn=3600
        )

        return {
            'success': True,
            'version': version,
            'downloadUrl': download_url
        }

    except HTTPException:
        raise
    except ClientError as e:
        logger.error(f"Error fetching version info from S3: {str(e)}")
        return {
            'success': False,
            'error': 'Failed to fetch version information',
            'version': '1.0.0'
        }
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {
            'success': False,
            'error': 'Internal server error',
            'version': '1.0.0'
        }

@router.get("/desktop/download/{version}")
async def download_desktop_app(
    version: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> RedirectResponse:
    """
    Get a redirect to a presigned S3 download URL for a specific version.
    Requires JWT authentication.
    """
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            raise HTTPException(status_code=401, detail="Invalid token")

        # Generate presigned URL
        s3_key = f"Aureus CA Setup {version}.exe"
        download_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': 'aureus-ca-application', 'Key': s3_key},
            ExpiresIn=3600
        )

        return RedirectResponse(url=download_url)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error") 