"""
BlueCat IPAM Provider - Allocate IP
====================================
Allocates an IP address from BlueCat Address Manager.

Author: Noah Farshad (noah.farshad@broadcom.com)
Version: 2.3.19

Uses CIDR-based network lookup to find the correct BlueCat network
regardless of the customer's block hierarchy.

DNS records created via v1 REST API + quickDeploy for immediate resolution.
"""

import logging
from ipaddress import ip_network
from vra_ipam_utils.ipam import IPAM
from vra_bluecat_utils.utils import (
    BlueCatClient,
    get_endpoint_properties
)


def handler(context, inputs):
    """
    Main entry point for AllocateIP action.
    """
    ipam = IPAM(context, inputs)
    IPAM.do_allocate_ip = do_allocate_ip
    
    return ipam.allocate_ip()


def do_allocate_ip(self, auth_credentials, cert):
    """
    Allocate IP address from BlueCat.
    
    Args:
        self: IPAM instance
        auth_credentials: Dict with credentials
        cert: Certificate path or True
    
    Returns:
        Allocation result dict
    """
    try:
        # DEBUG: Log all inputs
        logging.info(f"=== AllocateIP Debug ===")
        logging.info(f"Input keys: {list(self.inputs.keys())}")
        
        # Log ipAllocations structure
        allocations = self.inputs.get("ipAllocations", [])
        logging.info(f"Number of allocations: {len(allocations)}")
        for i, alloc in enumerate(allocations):
            logging.info(f"Allocation[{i}]: {alloc}")
        
        # Log resourceInfo
        resource_info = self.inputs.get("resourceInfo", {})
        logging.info(f"resourceInfo: {resource_info}")
        
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
        
        # Process allocations
        results = []
        
        for allocation in allocations:
            result = process_allocation(client, allocation, self.inputs)
            results.append(result)
        
        client.logout()
        
        return {"ipAllocations": results}
        
    except Exception as e:
        logging.exception("Error allocating IP")
        raise


def process_allocation(client, allocation, inputs):
    """
    Process a single IP allocation request.
    
    Args:
        client: BlueCatClient instance
        allocation: Allocation request dict
        inputs: Full inputs dict
    
    Returns:
        Allocation result dict
    """
    allocation_id = allocation.get("id")
    
    # vRA passes ipRangeIds as an ARRAY, not ipRangeId as a string!
    ip_range_ids = allocation.get("ipRangeIds", [])
    ip_range_id = ip_range_ids[0] if ip_range_ids else allocation.get("ipRangeId")
    
    # Get resource info for naming
    resource_info = inputs.get("resourceInfo", {})
    vm_name = resource_info.get("name", "vRA-VM")
    
    # Get CIDR from multiple possible locations
    cidr = None
    
    # Check allocation properties
    alloc_props = allocation.get("properties", {})
    cidr = (
        allocation.get("subnetCidr") or
        allocation.get("networkCIDR") or
        allocation.get("cidr") or
        alloc_props.get("__networkCidr") or
        alloc_props.get("networkCIDR") or
        alloc_props.get("cidr") or
        alloc_props.get("bluecatCidr")
    )
    
    # Check resourceInfo properties
    if not cidr:
        res_props = resource_info.get("properties", {})
        cidr = (
            res_props.get("__networkCidr") or
            res_props.get("networkCIDR") or
            res_props.get("cidr")
        )
    
    # Check top-level inputs
    if not cidr:
        cidr = inputs.get("subnetCidr") or inputs.get("networkCIDR")
    
    logging.info(f"Processing allocation {allocation_id}:")
    logging.info(f"  ipRangeIds: {ip_range_ids}")
    logging.info(f"  ipRangeId (resolved): {ip_range_id}")
    logging.info(f"  cidr: {cidr}")
    logging.info(f"  vm_name: {vm_name}")
    
    # Find BlueCat network
    network_id = None
    network_info = None
    
    # Strategy 1: Use provided IP range ID (this comes from the IP range we mapped!)
    if ip_range_id:
        logging.info(f"Trying ipRangeId as network ID: {ip_range_id}")
        network_info = client.get_network(ip_range_id)
        if network_info:
            network_id = ip_range_id
            logging.info(f"Found network by ID: {network_id}")
        else:
            logging.warning(f"Network ID {ip_range_id} not found directly")
    
    # Strategy 2: Search by CIDR
    if not network_info and cidr:
        logging.info(f"Searching for network by CIDR: {cidr}")
        network_info = client.search_network_by_cidr(cidr)
        if network_info:
            network_id = network_info.get("id")
            logging.info(f"Found network by CIDR: {network_id}")
    
    # Strategy 3: Try to extract CIDR from ipRangeId properties we stored
    if not network_info and ip_range_id:
        # The ipRangeId might be our BlueCat network ID as a string
        logging.info(f"Trying to fetch network details for ipRangeId: {ip_range_id}")
        try:
            network_info = client.get_network(int(ip_range_id))
            if network_info:
                network_id = int(ip_range_id)
                logging.info(f"Found network by numeric ID: {network_id}")
        except (ValueError, TypeError):
            logging.warning(f"Could not convert ipRangeId to int: {ip_range_id}")
    
    # Strategy 4: Search by segment name when CIDR is None (NSX Federation fix)
    # NSX Federation creates duplicate fabric networks. Only the Global Stretched
    # copy has the CIDR; TX-W01/VA-W01 copies have CIDR=None.
    # When CIDR is None, search BlueCat by segment name with prefix stripping.
    if not network_info:
        segment_name = None
        # Try to get segment name from allocation properties or inputs
        segment_name = (
            alloc_props.get("networkSegment") or
            alloc_props.get("__networkSegment") or
            inputs.get("customProperties", {}).get("networkSegment")
        )
        # Also try from resource tags
        if not segment_name:
            tags = inputs.get("tags", {})
            if isinstance(tags, dict):
                segment_name = tags.get("networkSegment")
            elif isinstance(tags, list):
                for t in tags:
                    if isinstance(t, dict) and t.get("key") == "networkSegment":
                        segment_name = t.get("value")
                        break
        
        if segment_name:
            logging.info(f"Strategy 4: Searching BlueCat by segment name: {segment_name}")
            
            # Try exact name first
            network_info = client.search_network_by_name(segment_name)
            if network_info:
                network_id = network_info.get("id")
                logging.info(f"Found network by exact name: {network_id}")
            else:
                # Strip G-/US-/TX-/VA- prefix and search by core name
                core_name = segment_name
                for prefix in ["G-", "US-", "TX-", "VA-"]:
                    if core_name.startswith(prefix):
                        core_name = core_name[len(prefix):]
                        break
                
                if core_name != segment_name:
                    logging.info(f"  Trying core name: {core_name}")
                    network_info = client.search_network_by_name(core_name)
                    if network_info:
                        network_id = network_info.get("id")
                        logging.info(f"Found network by core name: {network_id}")
    
    if not network_id or not network_info:
        raise Exception(f"Could not find BlueCat network for allocation {allocation_id}")
    
    # Allocate IP
    logging.info(f"Allocating IP from network {network_id} for {vm_name}")
    alloc_result = client.allocate_ip(network_id, hostname=vm_name)
    
    # Extract allocated IP
    allocated_ip = alloc_result.get("address") or str(alloc_result.get("id", ""))
    address_id = alloc_result.get("id")
    
    # Get network details for response
    gateway = ""
    prefix_length = 24
    
    if network_info:
        gateway = network_info.get("gateway", "")
        net_range = network_info.get("range", "")
        if net_range and "/" in net_range:
            try:
                net = ip_network(net_range, strict=False)
                prefix_length = net.prefixlen
            except:
                pass
    
    logging.info(f"Successfully allocated IP: {allocated_ip}")
    
    # Build response properties
    response_properties = {
        "bluecatAddressId": str(address_id),
        "bluecatNetworkId": str(network_id),
        "bluecatAllocatedIP": allocated_ip,
        "bluecatGateway": gateway
    }
    
    # Create DNS record if zone is configured
    endpoint_props = get_endpoint_properties(inputs)
    dns_zone = endpoint_props.get("dnsZone", "").strip()
    view_name = endpoint_props.get("viewName", "default").strip()
    config_name = endpoint_props.get("configuration", "").strip()
    
    if dns_zone and view_name:
        try:
            logging.info(f"DNS zone configured: {dns_zone}, view: {view_name}, config: {config_name}")
            logging.info(f"Creating DNS host record for {vm_name}.{dns_zone}")
            
            # Create DNS host record (A + PTR)
            dns_result = client.create_dns_record(
                hostname=vm_name,
                ip_address=allocated_ip,
                zone=dns_zone,
                view=view_name,
                config_name=config_name
            )
            
            if dns_result:
                host_record_id = dns_result.get("id")
                fqdn = f"{vm_name}.{dns_zone}"
                logging.info(f"DNS host record created: {fqdn} (ID: {host_record_id})")
                
                # Add DNS properties to response
                response_properties["bluecatHostRecordId"] = str(host_record_id)
                response_properties["bluecatFQDN"] = fqdn
                response_properties["bluecatDnsZone"] = dns_zone
                response_properties["bluecatDnsZoneId"] = str(dns_result.get("zoneId", ""))
                response_properties["bluecatDnsViewId"] = str(dns_result.get("viewId", ""))
            else:
                logging.warning("DNS record creation returned no data")
                
        except Exception as dns_error:
            logging.error(f"DNS record creation failed: {str(dns_error)}")
            # Don't fail the entire allocation if DNS fails
            response_properties["bluecatDnsError"] = str(dns_error)
    else:
        logging.info("DNS zone or view not configured, skipping DNS record creation")
        if not dns_zone:
            logging.info("  - dnsZone is empty or not set")
        if not view_name:
            logging.info("  - viewName is empty or not set")
    
    return {
        "ipAllocationId": allocation_id,
        "ipRangeId": str(network_id),
        "ipVersion": "IPv4",
        "ipAddresses": [allocated_ip],
        "gatewayAddress": gateway,
        "subnetPrefixLength": prefix_length,
        "properties": response_properties
    }
