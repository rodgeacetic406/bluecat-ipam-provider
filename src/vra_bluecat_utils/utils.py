"""
vra_bluecat_utils - BlueCat-specific Utilities
===============================================
Version: 2.3.17 - Fixed v1 API token parsing (BAMAuthToken double-prefix bug)

Customer-agnostic BlueCat Address Manager API client.

DNS DEVELOPMENT NOTES:
    v2.3.15: DNS host record creation via v2 API works but records don't resolve.
             BlueCat v2 API creates records without triggering change tracking,
             so quickDeploy reports "no differences to deploy."
    v2.3.16: Switched DNS creation to v1 REST API (addHostRecord) + quickDeploy.
             v1 API properly flags zones for deployment.
             
    FUTURE DEV (v2 API deployment):
        - BlueCat v2 POST /servers/{id}/deployments requires "type" field
        - Accepted types: FullDeployment, DifferentialDeployment, QuickDeployment,
          SelectiveDeployment, ValidationDeployment
        - SelectiveDeployment requires "resources" field with record references
        - Some service accounts may get 403 on DifferentialDeployment; quickDeploy (v1) works
        - Investigate v2 quickDeploy equivalent when BlueCat documents it
        - Zone hierarchy walking still uses v2 API (works well)
        - Your Hidden Primary DNS server ID is needed for zone deployment targeting
"""

import requests
import logging
from ipaddress import ip_network, IPv4Network, IPv4Address

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MAX_RECURSION_DEPTH = 15
MAX_NETWORKS_PER_BLOCK = 5000
MAX_TOTAL_NETWORKS = 10000
PAGE_SIZE = 100
MAX_PAGES_PER_LEVEL = 50


class BlueCatClient:
    """BlueCat Address Manager API Client"""
    
    def __init__(self, hostname, username, password, verify_ssl=False):
        self.hostname = hostname.rstrip('/')
        if not self.hostname.startswith('http'):
            self.hostname = f"https://{self.hostname}"
        
        self.base_url = f"{self.hostname}/api/v2"
        self.v1_base_url = f"{self.hostname}/Services/REST/v1"
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.session_id = None
        self.v1_token = None
        self._username = username
        self._password = password
        self._configurations = None
        self._stats = {"blocks_traversed": 0, "networks_found": 0, "max_depth_reached": 0}
        
        self._authenticate(username, password)
    
    def _authenticate(self, username, password):
        """Authenticate to BlueCat - returns 201, uses basicAuthenticationCredentials"""
        auth_url = f"{self.base_url}/sessions"
        
        response = self.session.post(
            auth_url,
            json={"username": username, "password": password},
            timeout=30
        )
        
        if response.status_code not in [200, 201]:
            response.raise_for_status()
        
        auth_data = response.json()
        basic_auth_creds = auth_data.get("basicAuthenticationCredentials")
        self.session_id = auth_data.get("id")
        api_token = auth_data.get("apiToken")
        
        if basic_auth_creds:
            self.session.headers.update({
                "Authorization": f"Basic {basic_auth_creds}",
                "Content-Type": "application/json"
            })
        elif api_token:
            import base64
            auth_string = base64.b64encode(f"{username}:{api_token}".encode()).decode()
            self.session.headers.update({
                "Authorization": f"Basic {auth_string}",
                "Content-Type": "application/json"
            })
        else:
            raise Exception("BlueCat authentication failed: no token received")
        
        logging.info(f"Authenticated to BlueCat at {self.hostname}")
    
    def _authenticate_v1(self):
        """Authenticate to BlueCat v1 REST API for DNS operations.
        
        v1 API is required for:
        - addHostRecord: Creates DNS records with proper change tracking
        - quickDeploy: Pushes pending changes to DNS servers
        
        v2 API creates records but does NOT trigger change tracking,
        making quickDeploy report 'no differences to deploy'.
        """
        if self.v1_token:
            return self.v1_token
        
        import urllib.parse
        encoded_password = urllib.parse.quote(self._password, safe='')
        
        url = f"{self.v1_base_url}/login?username={self._username}&password={encoded_password}"
        response = self.session.get(url, timeout=30)
        
        if response.status_code == 200:
            # Response format: "Session Token-> <token> <- for User : <username>"
            raw = response.text.strip().strip('"')
            if "-> " in raw and " <-" in raw:
                token_value = raw.split("-> ")[1].split(" <-")[0]
                # Token may include "BAMAuthToken: " prefix - strip it
                if token_value.startswith("BAMAuthToken: "):
                    self.v1_token = token_value.replace("BAMAuthToken: ", "", 1)
                else:
                    self.v1_token = token_value
            else:
                self.v1_token = raw
            
            logging.info("Authenticated to BlueCat v1 API")
            return self.v1_token
        else:
            logging.error(f"BlueCat v1 authentication failed: {response.status_code}")
            raise Exception(f"BlueCat v1 authentication failed: {response.status_code}")
    
    def _v1_request(self, method, endpoint, params=None):
        """Make an authenticated v1 API request.
        
        Args:
            method: HTTP method ('GET', 'POST', or 'DELETE')
            endpoint: v1 endpoint path (e.g., '/addHostRecord')
            params: Query parameters dict
            
        Returns:
            Response text (v1 API returns plain text, not JSON)
        """
        token = self._authenticate_v1()
        url = f"{self.v1_base_url}{endpoint}"
        headers = {"Authorization": f"BAMAuthToken: {token}"}
        
        if method.upper() == "GET":
            response = self.session.get(url, params=params, headers=headers, timeout=30)
        elif method.upper() == "DELETE":
            response = self.session.delete(url, params=params, headers=headers, timeout=30)
        else:
            response = self.session.post(url, params=params, headers=headers, timeout=30)
        
        if response.status_code != 200:
            raise Exception(f"v1 API error ({response.status_code}): {response.text}")
        
        return response.text
    
    def logout(self):
        # Logout v2
        if self.session_id:
            try:
                self.session.delete(f"{self.base_url}/sessions/{self.session_id}", timeout=10)
            except:
                pass
        # Logout v1
        if self.v1_token:
            try:
                headers = {"Authorization": f"BAMAuthToken: {self.v1_token}"}
                self.session.get(f"{self.v1_base_url}/logout", headers=headers, timeout=10)
            except:
                pass
    
    def _paginated_get(self, url, params=None, max_items=None):
        """Generic paginated GET request"""
        items = []
        params = params or {}
        params.setdefault("limit", PAGE_SIZE)
        pages_fetched = 0
        
        while pages_fetched < MAX_PAGES_PER_LEVEL:
            try:
                response = self.session.get(url, params=params, timeout=60)
                if response.status_code != 200:
                    break
                
                data = response.json()
                batch = data.get("data", [])
                if batch:
                    items.extend(batch)
                
                if max_items and len(items) >= max_items:
                    break
                
                links = data.get("_links", {})
                next_link = links.get("next", {})
                next_href = next_link.get("href") if isinstance(next_link, dict) else None
                
                if next_href and len(batch) >= PAGE_SIZE:
                    if next_href.startswith("/"):
                        url = f"{self.hostname}{next_href}"
                    else:
                        url = next_href
                    params = {}
                    pages_fetched += 1
                else:
                    break
            except Exception as e:
                logging.warning(f"Pagination error: {e}")
                break
        
        return items[:max_items] if max_items else items
    
    def get_configurations(self):
        if self._configurations is None:
            self._configurations = self._paginated_get(f"{self.base_url}/configurations", max_items=100)
            logging.info(f"Found {len(self._configurations)} BlueCat configuration(s)")
            for cfg in self._configurations:
                logging.info(f"  - {cfg.get('name')} (ID: {cfg.get('id')})")
        return self._configurations
    
    def get_views(self, config_id):
        url = f"{self.base_url}/configurations/{config_id}/views"
        views = self._paginated_get(url, max_items=100)
        if views:
            logging.info(f"Found {len(views)} view(s)")
        return views
    
    def get_blocks_from_view(self, view_id):
        url = f"{self.base_url}/views/{view_id}/blocks"
        return self._paginated_get(url, max_items=500)
    
    def get_blocks_from_config(self, config_id):
        url = f"{self.base_url}/configurations/{config_id}/blocks"
        return self._paginated_get(url, max_items=500)
    
    def get_all_top_level_blocks(self, config_id=None):
        if config_id is None:
            configs = self.get_configurations()
            if not configs:
                return []
            config_id = configs[0].get("id")
        
        all_blocks = []
        seen_block_ids = set()
        
        config_blocks = self.get_blocks_from_config(config_id)
        for block in config_blocks:
            bid = block.get("id")
            if bid and bid not in seen_block_ids:
                seen_block_ids.add(bid)
                all_blocks.append({"id": bid, "name": block.get("name", ""), "range": block.get("range", "")})
        
        views = self.get_views(config_id)
        for view in views:
            view_blocks = self.get_blocks_from_view(view.get("id"))
            for block in view_blocks:
                bid = block.get("id")
                if bid and bid not in seen_block_ids:
                    seen_block_ids.add(bid)
                    all_blocks.append({"id": bid, "name": block.get("name", ""), "range": block.get("range", "")})
        
        logging.info(f"Total top-level blocks: {len(all_blocks)}")
        return all_blocks
    
    def get_networks_from_all_blocks(self, limit=None, config_id=None):
        limit = limit or MAX_TOTAL_NETWORKS
        self._stats = {"blocks_traversed": 0, "networks_found": 0, "max_depth_reached": 0}
        
        top_blocks = self.get_all_top_level_blocks(config_id)
        if not top_blocks:
            return []
        
        all_networks = []
        for block_info in top_blocks:
            if len(all_networks) >= limit:
                break
            block_networks = self._traverse_block_deep(block_info.get("id"), depth=0, max_networks=limit - len(all_networks))
            all_networks.extend(block_networks)
        
        logging.info(f"Block traversal: {len(all_networks)} networks, max depth: {self._stats['max_depth_reached']}")
        return all_networks
    
    def _traverse_block_deep(self, block_id, depth=0, max_networks=5000):
        networks = []
        self._stats["blocks_traversed"] += 1
        if depth > self._stats["max_depth_reached"]:
            self._stats["max_depth_reached"] = depth
        
        if depth > MAX_RECURSION_DEPTH:
            return networks
        
        try:
            url = f"{self.base_url}/blocks/{block_id}/networks"
            block_nets = self._paginated_get(url, max_items=max_networks)
            if block_nets:
                networks.extend(block_nets)
            
            url = f"{self.base_url}/blocks/{block_id}/blocks"
            sub_blocks = self._paginated_get(url, max_items=500)
            for sub_block in sub_blocks:
                if len(networks) >= max_networks:
                    break
                sub_id = sub_block.get("id")
                if sub_id:
                    sub_networks = self._traverse_block_deep(sub_id, depth=depth + 1, max_networks=max_networks - len(networks))
                    networks.extend(sub_networks)
        except Exception as e:
            logging.warning(f"Error traversing block {block_id}: {e}")
        
        return networks
    
    def get_network(self, network_id):
        """
        Get network details by ID.
        
        This is the MISSING METHOD that was causing:
        "'BlueCatClient' object has no attribute 'get_network'"
        
        Args:
            network_id: BlueCat network ID (string or int)
        
        Returns:
            Network dict with id, name, range, gateway, etc. or None
        """
        try:
            url = f"{self.base_url}/networks/{network_id}"
            response = self.session.get(url, timeout=30)
            
            if response.status_code == 200:
                network = response.json()
                logging.info(f"Found network {network_id}: {network.get('name')} ({network.get('range')})")
                return network
            elif response.status_code == 404:
                logging.warning(f"Network {network_id} not found")
                return None
            else:
                logging.warning(f"Error getting network {network_id}: {response.status_code}")
                return None
        except Exception as e:
            logging.error(f"Error getting network {network_id}: {e}")
            return None
    
    def search_network_by_cidr(self, cidr):
        try:
            net = ip_network(cidr, strict=False)
            url = f"{self.base_url}/networks"
            params = {"filter": f"range:eq('{str(net)}')", "limit": 10}
            response = self.session.get(url, params=params, timeout=30)
            if response.status_code == 200:
                data = response.json().get("data", [])
                if data:
                    return data[0]
            return None
        except Exception as e:
            logging.error(f"Error searching by CIDR: {e}")
            return None
    
    def search_network_by_name(self, name):
        try:
            url = f"{self.base_url}/networks"
            params = {"filter": f"name:contains('{name}')", "limit": 20}
            response = self.session.get(url, params=params, timeout=30)
            if response.status_code == 200:
                data = response.json().get("data", [])
                if data:
                    return data[0]
            return None
        except Exception as e:
            return None
    
    def allocate_ip(self, network_id, hostname=None, mac=None):
        url = f"{self.base_url}/networks/{network_id}/addresses"
        payload = {"type": "IPv4Address", "state": "STATIC"}
        if hostname:
            payload["name"] = hostname
        if mac:
            payload["macAddress"] = mac
        response = self.session.post(url, json=payload, timeout=60)
        if response.status_code not in [200, 201]:
            response.raise_for_status()
        return response.json()
    
    def deallocate_ip(self, address_id):
        response = self.session.delete(f"{self.base_url}/addresses/{address_id}", timeout=30)
        if response.status_code == 404:
            return True
        response.raise_for_status()
        return True
    
    def search_address(self, ip_address):
        try:
            url = f"{self.base_url}/addresses"
            params = {"filter": f"address:eq('{ip_address}')", "limit": 5}
            response = self.session.get(url, params=params, timeout=30)
            if response.status_code == 200:
                data = response.json().get("data", [])
                if data:
                    return data[0]
            return None
        except:
            return None
    
    def update_address(self, address_id, **kwargs):
        response = self.session.patch(f"{self.base_url}/addresses/{address_id}", json=kwargs, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def create_dns_record(self, hostname, ip_address, zone, view="default", config_name=None):
        """
        Create DNS host record and deploy to DNS servers.
        
        Uses a hybrid approach:
        - v2 API: Zone hierarchy walking (reliable, well-structured)
        - v1 API: Record creation via addHostRecord (triggers change tracking)
        - v1 API: quickDeploy to push changes to DNS servers
        
        This is necessary because v2 API record creation does NOT trigger
        BlueCat's internal change tracking, so quickDeploy sees "no differences."
        v1 addHostRecord properly flags zones for deployment.
        
        Args:
            hostname: Short hostname (without domain)
            ip_address: IP address
            zone: DNS zone FQDN (e.g., 'corp.example.com')
            view: DNS view name (e.g., 'Internal')
            config_name: BlueCat configuration name (e.g., 'MyConfiguration')
        
        Returns:
            Dict with host record data including id, absoluteName, etc.
            None if failed.
        """
        try:
            # ============================================================
            # Step 1: Resolve view ID using v2 API
            # ============================================================
            if not config_name:
                configs = self.get_configurations()
                if configs:
                    config_id = configs[0].get("id")
                    config_name = configs[0].get("name")
                    logging.info(f"Using first configuration: {config_name} (ID: {config_id})")
                else:
                    logging.error("No BlueCat configurations found")
                    return None
            else:
                url = f"{self.base_url}/configurations"
                params = {"filter": f"name:eq('{config_name}')"}
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                
                configs = response.json().get("data", [])
                if not configs:
                    logging.error(f"Configuration '{config_name}' not found")
                    return None
                
                config_id = configs[0].get("id")
                logging.info(f"Found configuration: {config_name} (ID: {config_id})")
            
            # Get view ID
            url = f"{self.base_url}/configurations/{config_id}/views"
            params = {"filter": f"name:eq('{view}')"}
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            views = response.json().get("data", [])
            if not views:
                logging.error(f"DNS view '{view}' not found")
                return None
            
            view_id = views[0].get("id")
            logging.info(f"Found view: {view} (ID: {view_id})")
            
            # ============================================================
            # Step 2: Walk zone hierarchy using v2 API to get zone ID
            # (Needed for quickDeploy entityId)
            # ============================================================
            zone_parts = zone.rstrip(".").split(".")
            zone_parts.reverse()
            
            parent_url = f"{self.base_url}/views/{view_id}/zones"
            current_zone_id = None
            
            for i, part in enumerate(zone_parts):
                params = {"filter": f"name:eq('{part}')"}
                response = self.session.get(parent_url, params=params, timeout=30)
                response.raise_for_status()
                
                zones = response.json().get("data", [])
                if not zones:
                    logging.error(f"Zone part '{part}' not found at level {i} (searched: {parent_url})")
                    return None
                
                current_zone_id = zones[0].get("id")
                current_zone_name = zones[0].get("name")
                logging.info(f"  Zone walk [{i}]: {current_zone_name} (ID: {current_zone_id})")
                
                parent_url = f"{self.base_url}/zones/{current_zone_id}/zones"
            
            if not current_zone_id:
                logging.error(f"Could not resolve zone: {zone}")
                return None
            
            logging.info(f"Resolved zone '{zone}' to ID: {current_zone_id}")
            
            # ============================================================
            # Step 3: Create host record via v1 API (addHostRecord)
            # This properly triggers BlueCat's change tracking.
            # ============================================================
            fqdn = f"{hostname}.{zone}" if not hostname.endswith(zone) else hostname
            
            logging.info(f"Creating host record via v1 API: {fqdn} → {ip_address}")
            
            result_text = self._v1_request("POST", "/addHostRecord", params={
                "viewId": view_id,
                "absoluteName": fqdn,
                "addresses": ip_address,
                "ttl": -1,
                "properties": "reverseRecord=true|"
            })
            
            # v1 addHostRecord returns the record ID as plain text
            host_record_id = result_text.strip().strip('"')
            
            if not host_record_id or not host_record_id.isdigit():
                logging.error(f"addHostRecord returned unexpected value: {result_text}")
                return None
            
            logging.info(f"DNS record created: {fqdn} (ID: {host_record_id})")
            
            # ============================================================
            # Step 4: Quick Deploy the zone via v1 API
            # Pushes pending changes to DNS servers immediately.
            # ============================================================
            try:
                logging.info(f"Deploying zone {zone} (ID: {current_zone_id}) via quickDeploy...")
                
                deploy_result = self._v1_request("POST", "/quickDeploy", params={
                    "entityId": current_zone_id
                })
                
                deploy_text = deploy_result.strip().strip('"') if deploy_result else ""
                
                if deploy_text:
                    logging.info(f"quickDeploy response: {deploy_text}")
                else:
                    logging.info("quickDeploy completed successfully (no message = success)")
                    
            except Exception as deploy_error:
                # Don't fail the record creation if deploy fails
                # The record exists in BlueCat and can be deployed later
                logging.warning(f"quickDeploy warning: {str(deploy_error)}")
                logging.warning("DNS record created but deployment may be pending")
            
            # ============================================================
            # Step 5: Return record details
            # ============================================================
            return {
                "id": int(host_record_id),
                "absoluteName": fqdn,
                "name": hostname,
                "type": "HostRecord",
                "addresses": [ip_address],
                "reverseRecord": True,
                "zoneId": current_zone_id,
                "viewId": view_id
            }
                
        except Exception as e:
            logging.error(f"Exception creating DNS record: {str(e)}")
            return None
    
    def delete_dns_record(self, record_id, zone_id=None):
        """
        Delete a DNS host record and deploy the change.
        
        Uses v1 API for both deletion and deployment.
        
        Args:
            record_id: BlueCat host record ID
            zone_id: Zone ID for quickDeploy (optional but recommended)
            
        Returns:
            True if successful
        """
        try:
            logging.info(f"Deleting DNS record {record_id} via v1 API")
            
            self._v1_request("DELETE", "/delete", params={
                "objectId": record_id
            })
            
            logging.info(f"DNS record {record_id} deleted")
            
            # Deploy the change if zone_id provided
            if zone_id:
                try:
                    logging.info(f"Deploying zone {zone_id} after DNS deletion...")
                    self._v1_request("POST", "/quickDeploy", params={
                        "entityId": zone_id
                    })
                    logging.info("quickDeploy completed after DNS deletion")
                except Exception as deploy_error:
                    logging.warning(f"quickDeploy after deletion warning: {str(deploy_error)}")
            
            return True
            
        except Exception as e:
            logging.error(f"Error deleting DNS record {record_id}: {str(e)}")
            return False


def convert_network_to_ip_range(network, address_space_id=None):
    """Convert BlueCat network to vRA IP range format."""
    try:
        network_range = network.get("range", "")
        network_id = network.get("id")
        network_name = network.get("name") or network_range or f"network-{network_id}"
        gateway = network.get("gateway", "")
        
        if not network_range or "/" not in network_range or not network_id:
            return None
        
        net = IPv4Network(network_range, strict=False)
        prefix_length = net.prefixlen
        
        if prefix_length >= 31:
            return None
        
        network_address = int(net.network_address)
        broadcast_address = int(net.broadcast_address)
        
        if prefix_length == 30:
            start_ip = str(IPv4Address(network_address + 1))
            end_ip = str(IPv4Address(network_address + 2))
        else:
            start_ip = str(IPv4Address(network_address + 2))
            end_ip = str(IPv4Address(broadcast_address - 1))
        
        if IPv4Address(start_ip) >= IPv4Address(end_ip):
            return None
        
        final_address_space = address_space_id if address_space_id else "default"
        
        return {
            "id": str(network_id),
            "name": network_name,
            "description": "",
            "startIPAddress": start_ip,
            "endIPAddress": end_ip,
            "ipVersion": "IPv4",
            "addressSpaceId": final_address_space,
            "subnetPrefixLength": prefix_length,
            "gatewayAddress": gateway if gateway else "",
            "dnsServerAddresses": [],
            "domain": "",
            "tags": [
                {"key": "bluecatNetworkId", "value": str(network_id)},
                {"key": "bluecatCidr", "value": network_range}
            ],
            "properties": {
                "bluecatNetworkId": str(network_id),
                "bluecatNetworkName": network_name,
                "bluecatCidr": network_range,
                "subnetMask": str(net.netmask)
            }
        }
    except Exception as e:
        logging.error(f"Error converting network: {e}")
        return None


def get_endpoint_properties(inputs):
    endpoint = inputs.get("endpoint", inputs)
    return endpoint.get("endpointProperties", endpoint)
