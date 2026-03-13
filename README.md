# BlueCat IPAM Provider for VMware Aria Automation

A production-ready IPAM provider that integrates BlueCat Address Manager with VMware Aria Automation (formerly vRealize Automation). Handles IP allocation, deallocation, DNS record lifecycle, and full network discovery via BlueCat's block hierarchy.

**Version:** 2.3.19  
**Author:** Noah Farshad — Broadcom Professional Services  
**Tested on:** Aria Automation 8.x / VCF 5.x, BlueCat Address Manager 9.x

---

## What It Does

| Action | Description |
|--------|-------------|
| **AllocateIP** | Assigns the next available IP from a BlueCat network. Creates DNS host records (A + PTR) with immediate deployment via `quickDeploy`. |
| **DeallocateIP** | Releases IP addresses back to BlueCat. Cleans up associated DNS host records and deploys zone changes. |
| **GetIPRanges** | Discovers all networks using full recursive block traversal. Matches against vRA fabric network prefixes automatically. |
| **UpdateRecord** | Handles day-2 operations (VM rename, property changes) on BlueCat address records. |
| **ValidateEndpoint** | Tests connectivity, credentials, and configuration access to BlueCat Address Manager. |

---

## Quick Start

### 1. Import the Provider

Download `BlueCat_IPAM_v2.3.19.zip` from the [Releases](../../releases) page and import it into Aria Automation:

**Infrastructure → Integrations → Add Integration → IPAM → Import Provider Package**

### 2. Configure the Endpoint

After import, create an IPAM endpoint with these fields:

| Field | Description | Example |
|-------|-------------|---------|
| **BlueCat Hostname** | FQDN or IP of your BlueCat Address Manager | `bam.example.com` |
| **Username** | BlueCat API user | `api-user` |
| **Password** | BlueCat API password | `••••••••` |
| **Configuration Name** | BlueCat configuration (optional — uses first if blank) | `MyConfiguration` |
| **DNS Zone** | Zone for host record creation (optional) | `corp.example.com` |
| **DNS View Name** | BlueCat DNS view (optional) | `Internal` |
| **Block IDs** | Specific block IDs to scope discovery (optional) | *(leave blank for full traversal)* |

### 3. Run Data Collection

After saving the endpoint, Aria Automation will trigger **GetIPRanges** to discover your networks. This runs automatically on the polling interval (120 minutes) or you can trigger it manually.

---

## How Network Discovery Works

GetIPRanges uses a two-phase approach:

**Phase 1 — Identify target prefixes:** Queries your vRA fabric networks to extract unique /16 prefixes (e.g., `10.10`, `172.16`). This scopes the discovery to networks that actually matter for your deployments.

**Phase 2 — Full block traversal:** Recursively walks every block and sub-block in your BlueCat configuration, collecting all networks. Filters results to match the target prefixes from Phase 1.

### Customizing Network Discovery

If vRA fabric network querying fails (e.g., permissions), the provider falls back to a configurable prefix list in `get_ip_ranges/source.py`:

```python
# CUSTOMIZE: Add your /16 prefixes here as a fallback.
# These are only used if vRA fabric networks can't be queried.
# Example: If your networks are 10.10.x.x and 172.16.x.x:
#   target_prefixes = ["10.10", "172.16"]
target_prefixes = ["10.0"]
```

**For smaller environments:** A single prefix like `["10.10"]` may be all you need.

**For larger environments:** Add all relevant /16 ranges: `["10.10", "10.20", "172.16", "192.168"]`

To change the fallback prefixes:

1. Unzip `BlueCat_IPAM_v2.3.19.zip`
2. Unzip `bundle.zip`
3. Unzip `get_ip_ranges.zip`
4. Edit `source.py` — update the `target_prefixes` list
5. Re-zip in reverse order: `get_ip_ranges.zip` → `bundle.zip` → top-level zip

---

## How IP Allocation Works

AllocateIP uses a multi-strategy approach to find the correct BlueCat network:

1. **IP Range ID** — Uses the network ID mapped by GetIPRanges (primary path)
2. **CIDR Match** — Searches BlueCat by subnet CIDR from vRA allocation properties
3. **Numeric ID Lookup** — Tries the range ID as a direct BlueCat network ID
4. **Segment Name Search** — Falls back to NSX segment name matching (handles Federation duplicates)

### NSX Federation Support

NSX Federation environments create duplicate fabric networks from Global Manager and Local Manager. Only the Global Stretched copy carries the CIDR — local copies (TX-W01, VA-W01) have `CIDR=None`. The provider handles this by stripping site prefixes (`G-`, `US-`, `TX-`, `VA-`) and searching BlueCat by the core segment name.

---

## DNS Record Lifecycle

When a DNS zone and view are configured on the endpoint:

**On Allocate:** Creates an A record + reverse PTR via BlueCat v1 REST API (`addHostRecord`), then deploys immediately via `quickDeploy`. Records resolve within seconds.

**On Deallocate:** Deletes the host record and deploys the zone change.

> **Why v1 API for DNS?** BlueCat's v2 API creates records without triggering internal change tracking. `quickDeploy` via v2 reports "no differences to deploy." The v1 `addHostRecord` endpoint properly flags zones for deployment.

---

## Bundle Structure

The zip package follows Aria Automation's IPAM provider import format:

```
BlueCat_IPAM_v2.3.19.zip
├── registration.yaml           # Provider metadata and action mappings
├── endpoint-schema.json        # Endpoint configuration UI schema
├── logo.png                    # Provider icon
└── bundle.zip                  # ABX action bundle
    ├── allocate_ip.abx + .zip
    ├── deallocate_ip.abx + .zip
    ├── get_ip_ranges.abx + .zip
    ├── update_record.abx + .zip
    └── validate_endpoint.abx + .zip
```

Each `.abx` file defines the action metadata (runtime, entrypoint, timeout). Each `.zip` contains:
- `source.py` — Action handler code
- `vra_bluecat_utils/` — Shared BlueCat API client library
- `vra_ipam_utils/` — Aria Automation IPAM SDK
- Vendored Python libraries (requests, certifi, urllib3, etc.)

---

## Configuration Reference

### registration.yaml

| Property | Value | Notes |
|----------|-------|-------|
| `dcIntervalInMinutes` | `120` | How often GetIPRanges runs. Lower values increase API load on BlueCat. |
| `supportsAddressSpaces` | `true` | Maps to BlueCat configurations |
| `supportsUpdateRecord` | `true` | Enables day-2 record updates |
| `supportsOnDemandNetworks` | `false` | Network creation not supported |

### Tuning Parameters (utils.py)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_RECURSION_DEPTH` | `15` | Maximum block nesting depth for traversal |
| `MAX_NETWORKS_PER_BLOCK` | `5,000` / `50,000`* | Networks per block before moving on |
| `MAX_TOTAL_NETWORKS` | `10,000` / `50,000`* | Total networks across all blocks |
| `PAGE_SIZE` | `100` | BlueCat API pagination size |
| `MAX_PAGES_PER_LEVEL` | `50` | Maximum pages per API call |

*GetIPRanges uses higher limits (50,000) for full discovery; other actions use 10,000.

---

## Troubleshooting

**GetIPRanges returns 0 networks**
- Check that your fallback prefixes match your actual network ranges
- Verify the BlueCat user has read access to blocks and networks
- Check ABX action logs for "Block traversal" output showing discovered counts

**DNS records created but don't resolve**
- Ensure you're on v2.3.17+ (uses v1 API for DNS)
- Verify `quickDeploy` succeeds in the action logs
- Check that the BlueCat user has deploy permissions on the DNS zone

**AllocateIP can't find the network**
- Verify GetIPRanges has run at least once and discovered the network
- Check that the CIDR in vRA matches a BlueCat network exactly
- Look for "Strategy 1/2/3/4" in the action logs to see which lookup methods were tried

**Authentication failures**
- BlueCat v2 API returns 201 on successful login (not 200) — this is handled
- If using a service account, verify it has API access enabled in BlueCat

---

## Contributing

This provider was built from real-world enterprise deployments. If you've adapted it for your environment, contributions are welcome — especially around:

- Additional IPAM vendors or BlueCat API versions
- IPv6 support
- On-demand network creation
- Alternative DNS deployment methods

---

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE) for details.
