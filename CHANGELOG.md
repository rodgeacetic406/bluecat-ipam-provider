# Changelog

All notable changes to the BlueCat IPAM Provider for VMware Aria Automation.

## [v2.3.19] — 2026-03-05

### Updated
- Allocate IP: Improved CIDR extraction from multiple vRA input locations
- Bundle refresh with latest sanitized source for community release

## [v2.3.18] — 2026-03-05

### Fixed
- **Full block traversal** replaces IP sampling in GetIPRanges
  - v2.3.17 used `range:contains()` which only sampled 17 third-octets, missing networks with unsampled values
  - v2.3.18 walks every block and sub-block recursively with no network count limits
  - Finds ALL networks regardless of depth or position in the block hierarchy
- Increased polling interval to 120 minutes (was 10 minutes) to reduce unnecessary API load

### Changed
- GetIPRanges network limits raised to 50,000 (was 10,000) to support large enterprise environments

## [v2.3.17] — 2026-02-10

### Fixed
- **v1 API token parsing** — BAMAuthToken double-prefix bug
  - BlueCat v1 login response sometimes includes `BAMAuthToken: ` prefix inside the token value
  - Auth header was being sent as `BAMAuthToken: BAMAuthToken: <token>`, causing 401 errors
  - Now strips the embedded prefix if present

### Added
- DNS host record creation via v1 REST API (`addHostRecord`) + `quickDeploy`
  - v2 API creates records but does NOT trigger BlueCat's internal change tracking
  - v1 API properly flags zones for deployment, so records resolve immediately
- DNS record cleanup on deallocation (deletes host record + deploys zone)
- `bluecatHostRecordId`, `bluecatFQDN`, `bluecatDnsZone` properties stored with allocations

## [v2.3.16] — 2026-02-10

### Changed
- Switched DNS record creation from v2 API to v1 REST API
  - v2 API creates records without triggering change tracking
  - `quickDeploy` via v2 reports "no differences to deploy"
  - v1 `addHostRecord` properly flags zones for pending deployment

## [v2.3.15] — 2026-02-10

### Added
- DNS host record creation (A + PTR) during IP allocation
- DNS view and zone configuration via endpoint properties
- Zone hierarchy walking via v2 API to resolve zone IDs

### Known Issues
- DNS records created via v2 API don't resolve (fixed in v2.3.16)

## [v2.3.12] — 2026-02-10

### Added
- UpdateRecord action — handles VM rename and day-2 property updates
- Graceful fallback when BlueCat address record not found (won't fail deployments)

## [v2.1.0] — 2026-01-16

### Added
- ValidateEndpoint action — tests connectivity and credentials
- Configuration name validation against available BlueCat configurations
- SSL certificate support (file path or skip verification)

## [v2.0.0] — 2026-01-16

### Added
- Initial IPAM provider implementation for Aria Automation
- AllocateIP with multi-strategy network lookup (ID → CIDR → segment name)
- DeallocateIP with address cleanup
- GetIPRanges with block traversal and vRA fabric network prefix matching
- NSX Federation support — handles duplicate fabric networks from Global/Local Manager overlap
  - Strips G-/US-/TX-/VA- prefixes for segment name matching
- BlueCat v2 API client with pagination, session management, and recursive block walking
- Endpoint schema with hostname, credentials, configuration, DNS zone, view, and optional block IDs
- `vra_bluecat_utils` shared library bundled in each action
- `vra_ipam_utils` SDK integration for Aria Automation IPAM framework
