// Personal reports and templates. A template is a report an administrator has shared: everyone can generate it with their own dates, only
// its creator or an administrator can change it. A personal report is private to whoever made it. (The server enforces all of this; this
// only decides what to show.)
export const isTemplate = (report) => Boolean(report.shared);

// The list the server sends (your own private reports, and every template) as the two lists the page shows.
export const splitReports = (reports) => ({ mine: reports.filter((r) => !isTemplate(r)), templates: reports.filter(isTemplate) });

// What the signed-in person may do with one report. Anyone can generate, copy or export what they can see; changing or deleting it is for
// its creator or an administrator. Copying a template gives a personal report of your own.
export function reportActions(report, user) {
  const edit = Boolean(report.mine || user?.is_staff);
  return { generate: true, copy: true, export: true, edit, remove: edit, copyLabel: isTemplate(report) ? 'Copy To My Reports' : 'Copy' };
}
