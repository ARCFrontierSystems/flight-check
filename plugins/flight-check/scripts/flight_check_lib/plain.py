"""Plain-English wording for what Flight Check shows people: decisions, severities, and the release-critical controls.

The official decision names and control IDs stay in the records and JSON; these sentences are what a person reads first.
"""

from . import constants

DECISION = {
    constants.GATE_READY: "Ready to release. Flight Check found nothing that should stop a release.",
    constants.GATE_READY_ACCEPTED: "Ready to release, with risks you chose to accept.",
    constants.GATE_NOT_READY: "Not ready yet: there are problems to fix (or consciously accept) first.",
    constants.GATE_INSUFFICIENT: "Not ready yet: Flight Check could not confirm some important safety checks, so it "
                                 "cannot say the project is safe to release.",
    constants.GATE_CRITICAL: "Stop: there is a serious problem that should be fixed before anyone uses this release.",
}

SEVERITY = {"CRITICAL": "serious", "HIGH": "important", "MEDIUM": "moderate", "LOW": "minor", "INFORMATIONAL": "for information"}

# What each release-critical control means, in words a person without security training can act on.
CONTROL = {
    "APPSEC-SECRETS": "passwords, API keys, and other secrets are kept out of the code",
    "APPSEC-INJECTION": "user input cannot change database queries, shell commands, file paths, or web requests",
    "APPSEC-OUTPUT-ENCODING": "text from users is shown safely, so it cannot run as code in someone's browser",
    "APPSEC-WEBHOOKS": "incoming webhooks are checked to come from the real sender",
    "APPSEC-FILE-HANDLING": "uploaded files are checked and stored safely",
    "AUTHN-ENFORCED": "pages and APIs that need a login really require one",
    "AUTHN-CREDENTIALS": "passwords are stored and reset safely",
    "AUTHN-SESSIONS": "logins expire and end properly when someone logs out",
    "AUTHZ-SERVER-SIDE": "the server checks what each user is allowed to do",
    "AUTHZ-OBJECT-LEVEL": "users can only see and change their own data",
    "AUTHZ-ADMIN": "admin features are limited to admins",
    "DATA-STORE-ACCESS": "databases and file storage are not open to the internet",
    "PRIV-DELETION": "people can delete their account and data, as promised",
    "PRIV-DISCLOSURE-CONSISTENCY": "the privacy policy matches what the app actually collects and shares",
    "PAY-ENTITLEMENTS": "paid features are only unlocked after a confirmed payment",
    "PAY-WEBHOOK-INTEGRITY": "payment notifications are verified and handled once",
    "PAY-SECRET-BOUNDARY": "payment keys stay on the server and prices are set by the server",
    "SUPPLY-KNOWN-VULNS": "the packages the project depends on have no known security holes",
    "PROD-CONFIG": "the production settings turn off debug features",
    "REL-BACKUP-RESTORE": "production data is backed up and restoring it has been tested",
    "AI-TOOL-AUTHORIZATION": "actions the AI takes are limited to what the user may do",
    "AI-DATA-BOUNDARIES": "the AI cannot leak one user's data to another",
    "DOC-CLAIMS": "what the documentation and website promise matches what the code does",
}


def decision(name):
    return DECISION.get(name, name)


def control(cid, catalog_title=None):
    return CONTROL.get(cid) or (catalog_title or cid).rstrip(".").lower()
