"""
BlueCat IPAM Provider - Deallocate IP
======================================
Releases an IP address back to BlueCat Address Manager.
Also cleans up associated DNS host records and deploys changes.

Author: Noah Farshad (noah.farshad@broadcom.com)
Version: 2.3.17
"""

import logging
from vra_ipam_utils.ipam import IPAM
from vra_bluecat_utils.utils import (
    BlueCatClient,
    get_endpoint_properties
)


def handler(context, inputs):
    """
    Main entry point for DeallocateIP action.
    """
    ipam = IPAM(context, inputs)
    IPAM.do_deallocate_ip = do_deallocate_ip
    
    return ipam.deallocate_ip()


def do_deallocate_ip(self, auth_credentials, cert):
    """
    Deallocate/release IP addresses in BlueCat.
    
    Args:
        self: IPAM instance
        auth_credentials: Dict with credentials
        cert: Certificate path or True
    
    Returns:
        Deallocation result dict
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
        
        # Process deallocations
        deallocations = self.inputs.get("ipDeallocations", [])
        results = []
        
        for deallocation in deallocations:
            result = process_deallocation(client, deallocation)
            results.append(result)
        
        client.logout()
        
        return {"ipDeallocations": results}
        
    except Exception as e:
        logging.exception("Error deallocating IP")
        raise


def process_deallocation(client, deallocation):
    """
    Process a single IP deallocation request.
    
    Args:
        client: BlueCatClient instance
        deallocation: Deallocation request dict
    
    Returns:
        Deallocation result dict
    """
    deallocation_id = deallocation.get("id")
    ip_address = deallocation.get("ipAddress")
    
    # Get BlueCat address ID from properties
    properties = deallocation.get("properties", {})
    address_id = properties.get("bluecatAddressId")
    
    logging.info(f"Processing deallocation {deallocation_id}: IP={ip_address}, addressId={address_id}")
    
    # If no address_id, lookup by IP
    if not address_id and ip_address:
        logging.info(f"Looking up address ID for IP: {ip_address}")
        address_record = client.search_address(ip_address)
        if address_record:
            address_id = address_record.get("id")
            logging.info(f"Found address ID: {address_id}")
    
    if not address_id:
        logging.warning(f"Address not found for IP {ip_address}, may already be released")
        return {
            "ipDeallocationId": deallocation_id,
            "message": f"IP {ip_address} not found (may already be released)"
        }
    
    # Clean up DNS host record if present
    host_record_id = properties.get("bluecatHostRecordId")
    zone_id = None
    
    # Try to get zone ID from properties for quickDeploy
    dns_zone = properties.get("bluecatDnsZone", "")
    if host_record_id:
        # Get zone ID from the FQDN properties if available
        zone_id_str = properties.get("bluecatDnsZoneId", "")
        if zone_id_str:
            try:
                zone_id = int(zone_id_str)
            except (ValueError, TypeError):
                pass
        
        try:
            logging.info(f"Deleting DNS host record: {host_record_id}")
            client.delete_dns_record(int(host_record_id), zone_id=zone_id)
        except Exception as dns_err:
            logging.warning(f"DNS record cleanup failed: {str(dns_err)}")
    
    # Deallocate
    client.deallocate_ip(address_id)
    
    logging.info(f"Successfully deallocated address ID: {address_id}")
    
    return {
        "ipDeallocationId": deallocation_id,
        "message": "Successfully deallocated"
    }
