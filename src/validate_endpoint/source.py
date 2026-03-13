"""
BlueCat IPAM Provider - Validate Endpoint
==========================================
Validates connectivity and credentials to BlueCat Address Manager.

Author: Noah Farshad (noah.farshad@broadcom.com)
Version: 2.1.0
"""

import logging
from vra_ipam_utils.ipam import IPAM, InvalidCertificateException
from vra_bluecat_utils.utils import BlueCatClient, get_endpoint_properties


def handler(context, inputs):
    """
    Main entry point for ValidateEndpoint action.
    
    Uses the IPAM SDK pattern for credential handling.
    """
    ipam = IPAM(context, inputs)
    IPAM.do_validate_endpoint = do_validate_endpoint
    
    return ipam.validate_endpoint()


def do_validate_endpoint(self, auth_credentials, cert):
    """
    Validate BlueCat Address Manager endpoint.
    
    Args:
        self: IPAM instance
        auth_credentials: Dict with privateKeyId (username) and privateKey (password)
        cert: Certificate file path or True if no cert needed
    
    Returns:
        Validation result dict
    """
    try:
        # Get endpoint properties
        endpoint_props = get_endpoint_properties(self.inputs)
        hostname = endpoint_props.get("hostName", "").strip()
        
        # Get credentials from auth_credentials (retrieved via context.request)
        username = auth_credentials.get("privateKeyId", "")
        password = auth_credentials.get("privateKey", "")
        
        if not hostname:
            return {
                "error": "Missing hostname",
                "message": "BlueCat hostname is required",
                "status": "FAILED"
            }
        
        if not username or not password:
            return {
                "error": "Missing credentials", 
                "message": "Username and password are required",
                "status": "FAILED"
            }
        
        # Determine SSL verification
        verify_ssl = False
        if cert and isinstance(cert, str):
            verify_ssl = cert  # Use cert file path
        
        logging.info(f"Validating BlueCat endpoint: {hostname}")
        
        # Create client and test connection
        client = BlueCatClient(hostname, username, password, verify_ssl=verify_ssl)
        
        # Get configurations to validate access
        configs = client.get_configurations()
        
        if not configs:
            client.logout()
            return {
                "error": "No configurations found",
                "message": "No BlueCat configurations accessible with these credentials",
                "status": "FAILED"
            }
        
        config_names = [c.get("name") for c in configs]
        
        # Check for specific configuration if requested
        config_name = endpoint_props.get("configuration", "").strip()
        if config_name and config_name not in config_names:
            client.logout()
            return {
                "error": "Configuration not found",
                "message": f"Configuration '{config_name}' not found. Available: {', '.join(config_names)}",
                "status": "FAILED"
            }
        
        client.logout()
        
        logging.info("BlueCat endpoint validation successful")
        
        return {
            "message": f"Successfully connected to BlueCat at {hostname}",
            "status": "SUCCESS",
            "statusCode": "200"
        }
        
    except InvalidCertificateException:
        raise  # Let IPAM class handle certificate errors
        
    except Exception as e:
        logging.exception("Validation error")
        return {
            "error": str(e),
            "message": f"Validation failed: {str(e)}",
            "status": "FAILED"
        }
