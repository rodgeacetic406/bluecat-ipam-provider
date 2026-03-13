"""
BlueCat IPAM Provider - Get IP Ranges (Full Block Traversal)
===============================================================
Version: 2.3.18

v2.3.18 FIX:
  v2.3.17 used IP sampling with range:contains() which only sampled 17 third-octets.
  Networks with unsampled third-octets (e.g., /28 subnets) were missed.
  
  v2.3.18 uses FULL BLOCK TRAVERSAL with no caps:
  - Walks every block and sub-block recursively
  - No network count limits (was 10000, now unlimited within timeout)
  - Finds ALL networks regardless of depth or position
  
  Also: Polling interval increased to 120 minutes (was 10 minutes)

Author: Noah Farshad (noah.farshad@broadcom.com)
"""

import logging
import json
from ipaddress import ip_network, IPv4Network
from vra_ipam_utils.ipam import IPAM
from vra_bluecat_utils.utils import (
    BlueCatClient,
    convert_network_to_ip_range,
    get_endpoint_properties
)


# Global context reference
_context = None


def handler(context, inputs):
    """Main entry point for GetIPRanges action."""
    global _context
    _context = context
    
    ipam = IPAM(context, inputs)
    IPAM.do_get_ip_ranges = do_get_ip_ranges
    return ipam.get_ip_ranges()


def do_get_ip_ranges(self, auth_credentials, cert):
    """Get IP ranges from BlueCat using full block traversal."""
    try:
        endpoint_props = get_endpoint_properties(self.inputs)
        hostname = endpoint_props.get("hostName", "").strip()
        
        username = auth_credentials.get("privateKeyId", "")
        password = auth_credentials.get("privateKey", "")
        
        verify_ssl = False
        if cert and isinstance(cert, str):
            verify_ssl = cert
        
        logging.info(f"")
        logging.info(f"{'='*60}")
        logging.info(f"BlueCat GetIPRanges v2.3.18 - Full Block Traversal")
        logging.info(f"{'='*60}")
        logging.info(f"Connecting to BlueCat at {hostname}")
        
        client = BlueCatClient(hostname, username, password, verify_ssl=verify_ssl)
        
        configs = client.get_configurations()
        config_name = configs[0].get("name", "default") if configs else "default"
        logging.info(f"Using Address Space: {config_name}")
        
        # Check for specific CIDR requests (VM deployment scenario)
        requested_cidrs = extract_cidrs_from_inputs(self.inputs)
        if requested_cidrs:
            logging.info(f"Specific CIDRs requested: {requested_cidrs}")
            ip_ranges = []
            for cidr in requested_cidrs:
                network = client.search_network_by_cidr(cidr)
                if network:
                    ip_range = convert_network_to_ip_range(network, address_space_id=config_name)
                    if ip_range:
                        ip_ranges.append(ip_range)
            if ip_ranges:
                client.logout()
                return {"ipRanges": ip_ranges}
        
        # PHASE 1: Get target prefixes from vRA fabric networks
        logging.info(f"")
        logging.info(f"{'='*60}")
        logging.info(f"PHASE 1: Identifying target IP ranges")
        logging.info(f"{'='*60}")
        
        target_prefixes = get_target_prefixes_from_vra(self)
        
        if not target_prefixes:
            # ================================================================
            # CUSTOMIZE: Add your /16 prefixes here as a fallback.
            # These are only used if vRA fabric networks can't be queried.
            # Example: If your networks are 10.10.x.x and 172.16.x.x:
            #   target_prefixes = ["10.10", "172.16"]
            # For smaller environments, a single prefix may be sufficient.
            # ================================================================
            target_prefixes = ["10.0"]
            logging.info(f"Using fallback prefixes: {target_prefixes}")
            logging.info(f"NOTE: Update fallback prefixes in source.py to match your environment")
        
        logging.info(f"Target /16 prefixes: {len(target_prefixes)}")
        for prefix in sorted(target_prefixes):
            logging.info(f"  - {prefix}.x.x")
        
        # PHASE 2: Full block traversal - find ALL networks
        logging.info(f"")
        logging.info(f"{'='*60}")
        logging.info(f"PHASE 2: Full block traversal (no limits)")
        logging.info(f"{'='*60}")
        
        all_bluecat_networks = client.get_networks_from_all_blocks(config_id=configs[0].get("id") if configs else None)
        logging.info(f"Total networks from block traversal: {len(all_bluecat_networks)}")
        
        # Filter to only networks matching our target prefixes
        target_set = set(target_prefixes)
        matched_networks = {}
        
        for net in all_bluecat_networks:
            net_range = net.get("range", "")
            net_id = net.get("id")
            if net_range and net_id and "." in net_range:
                parts = net_range.split(".")
                prefix = f"{parts[0]}.{parts[1]}"
                if prefix in target_set:
                    matched_networks[net_id] = net
        
        logging.info(f"Networks matching target prefixes: {len(matched_networks)}")
        
        # Convert to vRA format
        logging.info(f"")
        logging.info(f"{'='*60}")
        logging.info(f"PHASE 3: Converting to vRA format")
        logging.info(f"{'='*60}")
        
        ip_ranges = []
        converted = 0
        skipped = 0
        
        for network in matched_networks.values():
            ip_range = convert_network_to_ip_range(network, address_space_id=config_name)
            if ip_range:
                ip_ranges.append(ip_range)
                converted += 1
            else:
                skipped += 1
        
        logging.info(f"  Converted: {converted}")
        logging.info(f"  Skipped (too small): {skipped}")
        
        client.logout()
        
        # Final summary
        logging.info(f"")
        logging.info(f"{'='*60}")
        logging.info(f"FINAL SUMMARY")
        logging.info(f"{'='*60}")
        logging.info(f"Total IP ranges: {len(ip_ranges)}")
        
        if ip_ranges:
            prefix_counts = {}
            for ipr in ip_ranges:
                cidr = ipr.get("properties", {}).get("bluecatCidr", "")
                if cidr and "." in cidr:
                    parts = cidr.split(".")
                    prefix = f"{parts[0]}.{parts[1]}.x.x"
                    prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
            
            logging.info(f"Breakdown by IP prefix:")
            for prefix in sorted(prefix_counts.keys()):
                logging.info(f"  {prefix}: {prefix_counts[prefix]} networks")
        
        return {"ipRanges": ip_ranges}
        
    except Exception as e:
        logging.exception("Error getting IP ranges")
        return {"ipRanges": [], "error": str(e)}


def get_target_prefixes_from_vra(ipam_self):
    """Get unique /16 prefixes from vRA fabric networks."""
    prefixes = set()
    
    try:
        logging.info("Attempting to get fabric network CIDRs from vRA...")
        
        fabric_cidrs = []
        
        if _context:
            try:
                for method in [
                    lambda: _context.request("/iaas/api/fabric-networks?$top=500", "GET", {}),
                    lambda: _context.request("/iaas/api/fabric-networks?$top=500", "GET"),
                ]:
                    try:
                        response = method()
                        if response:
                            data = parse_response(response)
                            if data:
                                for net in data.get("content", []):
                                    cidr = net.get("cidr")
                                    if cidr:
                                        fabric_cidrs.append(cidr)
                                break
                    except:
                        continue
            except Exception as e:
                logging.debug(f"Context request failed: {e}")
        
        if fabric_cidrs:
            logging.info(f"Found {len(fabric_cidrs)} fabric network CIDRs")
            for cidr in fabric_cidrs:
                try:
                    if "." in cidr:
                        parts = cidr.split(".")
                        prefix = f"{parts[0]}.{parts[1]}"
                        prefixes.add(prefix)
                except:
                    pass
            logging.info(f"Extracted {len(prefixes)} unique /16 prefixes")
        else:
            logging.info("Could not retrieve fabric networks from vRA")
            
    except Exception as e:
        logging.warning(f"Error getting prefixes from vRA: {e}")
    
    return list(prefixes)


def parse_response(response):
    """Parse various response formats."""
    try:
        if hasattr(response, 'content'):
            content = response.content
            if isinstance(content, bytes):
                content = content.decode('utf-8')
            return json.loads(content)
        elif isinstance(response, dict):
            return response
        elif hasattr(response, 'json'):
            return response.json()
    except:
        pass
    return None


def extract_cidrs_from_inputs(inputs):
    """Extract CIDR references from inputs."""
    cidrs = []
    
    network_context = inputs.get("networkSelectionIds", [])
    for net in (network_context if isinstance(network_context, list) else []):
        if isinstance(net, dict):
            cidr = net.get("cidr") or net.get("subnetCIDR") or net.get("networkCIDR")
            if cidr:
                cidrs.append(cidr)
        elif isinstance(net, str) and "/" in net:
            cidrs.append(net)
    
    for key in ["subnetCIDR", "networkCIDR", "cidr", "__networkCidr"]:
        if inputs.get(key):
            cidrs.append(inputs[key])
    
    return list(set(cidrs))
