// Definitions shown when you hover a term. Each says what the number or value IS in this dashboard, taken from how it is
// calculated, and avoids guessing how the source systems are configured (it says "as set in Autotask" instead).
const TERMS = {
  // tickets: tiles and columns
  'ticket count': 'Tickets created in the selected period that match the active filters.',
  'open': 'Tickets with no completion time yet.',
  'performance': 'How hard this server is working: CPU, RAM and disk. The dashboard does not read these from anywhere yet, so they show No data.',
  'patches': 'Patching for this server in each of the three Overview categories, from the N-central device with the same name in the same client.',
  'data collected': 'The latest date found for this server on a successful run of each collector, so you can see how old each source\'s data about it is.',
  'last added': 'The date of the newest patch installed on this server in this category, from the N-central device with the same name in the same client. Installs with no date are not counted.',
  'next due': 'When the next patch in this category is due. Not known yet: the N-central patch schedule is not part of what is collected.',
  'patches up to date': 'Ticked when the newest patch inventory for this server in N-central lists patches of this kind and none is missing. It does not say whether the server is healthy overall: that is not defined yet.',
  'data as of': 'When the data on this page was last collected from its source, taken from the last collection that succeeded, and whether that is as recent as the collector\'s schedule expects. A failed collection does not refresh it: the figures stay as old as they were.',
  'tickets created per day': 'How many tickets were created on each day of the period (dates in UTC), after the filters that are on.',
  'tickets created per week (weeks start on monday)': 'How many tickets were created in each week of the period, weeks starting on Monday (dates in UTC), after the filters that are on.',
  'open tickets': 'Tickets with no completion time yet. Click to show only these.',
  'closed': 'Tickets that have a completion time.',
  'flagged tickets': 'Tickets shown in red. The only rule today: the first response came after its due time. Click to show only these.',
  'sla missed': 'Tickets where Autotask recorded that the resolution SLA was not met. Click to show only these.',
  'sla met': 'Whether Autotask recorded the resolution SLA as met. Blank when there is no SLA result.',
  'response met': 'Whether Autotask recorded the first-response target as met. Blank until there is a first response.',
  'avg response time': 'Average time from a ticket being created to its first response, over tickets that have had one. Elapsed time, not business hours.',
  'avg resolution time': 'Average time from creation to completion, over completed tickets. Elapsed time, not business hours.',
  'response time': 'Time from creation to first response. Elapsed time, not business hours.',
  'resolution time': 'Time from creation to completion. Elapsed time, not business hours.',
  'client': 'The company the ticket belongs to in Autotask.',
  'type': 'Ticket type as set in Autotask (for example Incident, Alert or Service Request).',
  'priority': 'Priority as set in Autotask. The colour of each priority is chosen in Settings.',
  'issue type': 'The issue category chosen on the ticket in Autotask.',
  'sub type': 'The issue sub-category chosen on the ticket in Autotask.',
  'status': 'The ticket status in Autotask.',
  'technician': 'Who the ticket is assigned to. Only available once the source collects assignments.',
  'unspecified': 'The source had no value for this field.',
  'ticket by client': 'Tickets per client. Click a slice or a legend entry to filter the whole page by it; click again to remove the filter.',
  'ticket by priority': 'Tickets per priority. Click a slice or a legend entry to filter the whole page by it; click again to remove the filter.',
  'ticket by type': 'Tickets per ticket type. Click a slice or a legend entry to filter the whole page by it; click again to remove the filter.',
  'ticket by issue type': 'Tickets per issue type. Click a slice or a legend entry to filter the whole page by it; click again to remove the filter.',
  'ticket by sub type': 'Tickets per issue sub-type. Click a bar to filter the whole page by it; click again to remove the filter.',
  'ticket by technician': 'Tickets per technician. Click a slice or a legend entry to filter the whole page by it; click again to remove the filter.',
  // patches
  'overall score': 'Weighted average of the three patch type scores. Weights start at 1 and are changed under Score Weights.',
  'current score': 'Calculated from each device\'s newest usable patch inventory. A device whose inventory could not be read is never counted as compliant.',
  'previous score': 'The same calculation from each device\'s inventory before the newest one.',
  'windows security patching': 'Compliance of patches flagged as security updates (and not third-party).',
  'windows patching': 'Compliance of the other Windows patches (not security, not third-party).',
  'third party patching': 'Compliance of non-Microsoft application patches.',
  'compliance': 'Installed patches as a share of all patch entries (installed plus missing).',
  'installed': 'Patches that are installed. In the patch list a tick means installed and a cross means missing; it is never both.',
  'missing': 'Patches that are available but not installed.',
  'total': 'Installed plus missing.',
  'category': 'The update classification reported by N-central (for example Security Updates or Update Rollups).',
  'kb': 'The Microsoft Knowledge Base article number of the update, when its title carries one.',
  'device': 'The device name, falling back to the hostname or ID when the source has no name for it.',
  'score weights': 'How much each item counts towards its score. A blank box uses the shared default; 0 leaves the item out.',
  // backups (Cove)
  'backups|devices': 'Backup devices Cove currently reports that match the active filters.',
  'ok': 'Devices whose health is OK: the collector found no red flags. Click to show only these.',
  'warning': 'Devices with at least one red flag that is not a failed backup. Click to show only these.',
  'critical': 'Devices with no successful backup, or a failed, aborted or interrupted backup session. Click to show only these.',
  'red flags': 'Problems the Cove collector found, such as a failed backup, short retention, an outdated client, an end-of-life operating system or an unreachable local backup vault.',
  'used storage': 'Backup storage used in Cove by the devices shown, in binary units (1 GB is 1024 x 1024 x 1024 bytes), as Cove reports it.',
  'backups|servers': 'Devices Cove classes as servers.',
  'microsoft 365': 'Microsoft 365 accounts protected through Cove, rather than computers.',
  'health': 'OK, Warning or Critical, set by the Cove collector from the device\'s red flags (see Warning and Critical).',
  'device type': 'Server, Workstation or Microsoft 365, as classified by Cove.',
  'customer': 'The customer name Cove reports for the device.',
  'last success': 'When the device last completed a backup successfully, as reported by Cove.',
  'days since success': 'Whole days since the last successful backup, calculated by the collector.',
  'retention (days)': 'How long Cove keeps this device\'s backups. The collector flags under 30 days as critically short and under 90 as short.',
  'cloud storage': 'Status of the device\'s cloud storage as reported by Cove.',
  'lsv': 'Status of the local backup vault (Cove\'s LSV) as reported by Cove. Only meaningful when it is enabled.',
  'immutable': 'Whether immutable backup copies, which cannot be altered or deleted, are enabled for the device.',
  'backups by health': 'Devices per health level. Click a slice or a legend entry to filter the whole page by it; click again to remove the filter.',
  'backups by customer': 'Devices per customer. Click a slice or a legend entry to filter the whole page by it; click again to remove the filter.',
  'backups by device type': 'Devices per type. Click a slice or a legend entry to filter the whole page by it; click again to remove the filter.',
  'red flags by category': 'Red flags per category, such as backup currency, retention or lifecycle. Click a bar to show only devices with a flag in that category.',
  // server health (SentinelOne)
  'servers|servers': 'Endpoints SentinelOne classes as servers (machine type server). Click to show only these.',
  'servers|sentinelone disconnected': 'Endpoints whose SentinelOne network status reads as disconnected, offline or down. Click to show only these.',
  'servers|n-central disconnected': 'Servers whose device N-central cannot find: its newest patch collection answered 404 (device not found). Click to show only these.',
  'servers|infected': 'Endpoints SentinelOne reports as infected. Click to show only these.',
  'servers|out of date': 'Endpoints running an agent version that is not the latest SentinelOne reports as available. Click to show only these.',
  'cpu': 'Processor use. Not collected yet: the performance collection that would supply it has no data so far.',
  'ram': 'Memory use. Not collected yet: the performance collection that would supply it has no data so far.',
  'disk': 'Disk space used. Not collected yet: the performance collection that would supply it has no data so far.',
  'network status': 'Connection state reported by the SentinelOne agent.',
  'sentinelone status': 'Whether SentinelOne can reach this server, as its agent reports it: connected, or disconnected, offline or down.',
  'n-central status': 'Whether N-central can find this device, from its newest patch collection: Connected if it worked, Disconnected if N-central answered 404 (not found). Unknown if no N-central device matches this server.',
  'data age': 'Time since this agent\'s data was collected (not since the device was last active).',
  'machine type': 'The machine type reported by SentinelOne (for example server, desktop or laptop).',
  'os': 'The operating system family reported by SentinelOne.',
  'agent version': 'The installed SentinelOne agent version.',
  'mitigation mode': 'What the agent does with a threat: protect mitigates it automatically, detect only reports it.',
  'up to date': 'Whether the agent runs the latest version SentinelOne reports as available.',
  'clean': 'No active infection reported: the opposite of SentinelOne\'s infected flag.',
};

// A page can give a label its own meaning: the term is looked up as 'scope|term' first, then on its own.
const norm = (term) => String(term ?? '').trim().toLowerCase();
export const define = (term, scope) => (scope && TERMS[`${scope}|${norm(term)}`]) || TERMS[norm(term)];

// Props to spread on an element so its definition shows on hover, and a dotted underline marks it as defined.
export function hint(term, className = '', scope) {
  const text = define(term, scope);
  if (!text) return className ? { class: className } : {};
  return { title: text, class: `${className} has-tip`.trim() };
}

export const TERM_KEYS = Object.keys(TERMS);
