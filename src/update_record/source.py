"""
BlueCat IPAM Provider - Update Record
======================================
Updates an IP address record in BlueCat Address Manager.

Author: Noah Farshad (noah.farshad@broadcom.com)
Version: 2.3.12
"""

import logging
from vra_ipam_utils.ipam import IPAM
from vra_bluecat_utils.utils import (
    BlueCatClient,
    get_endpoint_properties
)


def handler(context, inputs):
    """
    Main entry point for UpdateRecord action.
    """
    ipam = IPAM(context, inputs)
    IPAM.do_update_record = do_update_record
    
    return ipam.update_record()


def do_update_record(self, auth_credentials, cert):
    """
    Update IP address record in BlueCat.
    
    Typically called when VM properties change (rename, etc.)
    or during day-2 operations on the deployment.
    
    Args:
        self: IPAM instance
        auth_credentials: Dict with credentials
        cert: Certificate path or True
    
    Returns:
        Update result dict
    """
    try:
        # Get endpoint properties
        endpoint_props = get_endpoint_properties(self.inputs)
        hostname = endpoint_props.get("hostName", "").strip()
        
        # Get credentials
        username = auth_credentials.get("privateKeyId", "")
        password = auth_credentials.get("privateKey", "")
        
        # SSL verification
        verify_ssl = False
        if cert and isinstance(cert, str):
            verify_ssl = cert
        
        # Create client
        client = BlueCatClient(hostname, username, password, verify_ssl=verify_ssl)
        
        # ============================================================
        # Extract IP and properties from vRA inputs
        # vRA sends addressInfos[] for update_record, NOT updateRecord
        # ============================================================
        address_infos = self.inputs.get("addressInfos", [])
        resource_info = self.inputs.get("resourceInfo", {})
        resource_props = resource_info.get("properties", {})
        
        # Get IP address from addressInfos (primary source)
        ip_address = None
        if address_infos:
            ip_address = address_infos[0].get("address")
        
        # Fallback: try resourceInfo properties
        if not ip_address:
            ip_address = resource_props.get("address") or resource_props.get("bluecatAllocatedIP")
        
        # Get VM name for hostname update
        new_hostname = resource_info.get("name")
        
        # Get BlueCat address ID from resource properties (stored during allocate)
        address_id = resource_props.get("bluecatAddressId")
        
        logging.info(f"Update record called:")
        logging.info(f"  IP address: {ip_address}")
        logging.info(f"  VM name: {new_hostname}")
        logging.info(f"  BlueCat address ID: {address_id}")
        logging.info(f"  addressInfos count: {len(address_infos)}")
        
        # If no address_id, lookup by IP
        if not address_id and ip_address:
            logging.info(f"Looking up address ID for IP: {ip_address}")
            address_record = client.search_address(ip_address)
            if address_record:
                address_id = address_record.get("id")
                logging.info(f"Found address ID: {address_id}")
        
        if not address_id:
            logging.warning(f"Could not find BlueCat address for IP {ip_address}")
            client.logout()
            # Return success anyway - don't fail the deployment over an update record issue
            return {
                "status": "SUCCESS",
                "message": f"Address record not found for {ip_address}, skipping update"
            }
        
        # Build update payload
        update_data = {}
        if new_hostname:
            update_data["name"] = new_hostname
        
        if not update_data:
            logging.info("No updates to apply")
            client.logout()
            return {
                "status": "SUCCESS",
                "message": "No updates required"
            }
        
        # Update the address
        client.update_address(address_id, **update_data)
        
        client.logout()
        
        logging.info(f"Successfully updated address ID: {address_id}")
        
        return {
            "status": "SUCCESS",
            "message": "Record updated successfully"
        }
        
    except Exception as e:
        logging.exception("Error updating record")
        # Don't fail deployments over update record issues
        return {
            "status": "SUCCESS",
            "message": f"Update record completed with warning: {str(e)}"
        }
