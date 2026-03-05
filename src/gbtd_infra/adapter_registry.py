from __future__ import annotations

from gbtd_infra.adapters.bugzilla import BugzillaAdapter
from gbtd_infra.adapters.debian import DebianBTSAdapter
from gbtd_infra.adapters.github import GitHubIssuesAdapter
from gbtd_infra.adapters.gitlab import GitLabIssuesAdapter
from gbtd_infra.adapters.google import GoogleIssueTrackerAdapter
from gbtd_infra.adapters.jira import JiraAdapter
from gbtd_infra.adapters.launchpad import LaunchpadAdapter
from gbtd_infra.adapters.phabricator import PhabricatorAdapter
from gbtd_infra.adapters.redmine import RedmineAdapter
from gbtd_infra.adapters.youtrack import YouTrackAdapter


def adapter_for_family(family_slug: str):
    mapping = {
        "bugzilla": BugzillaAdapter,
        "github": GitHubIssuesAdapter,
        "gitlab": GitLabIssuesAdapter,
        "jira": JiraAdapter,
        "launchpad": LaunchpadAdapter,
        "redmine": RedmineAdapter,
        "youtrack": YouTrackAdapter,
        "google": GoogleIssueTrackerAdapter,
        "google_issue_tracker": GoogleIssueTrackerAdapter,
        "debian": DebianBTSAdapter,
        "debian_bts": DebianBTSAdapter,
        "phabricator": PhabricatorAdapter,
    }
    return mapping.get(family_slug)
