import base64
import io
import os
import re
import json
import time
from datetime import datetime

import qrcode
import requests

from django.contrib.auth.decorators import login_required
from django.http import (
    JsonResponse,
    HttpResponse,
    HttpResponseRedirect,
    HttpResponseNotFound,
)
from django.template import loader
from django.shortcuts import get_object_or_404
from django.core.mail import send_mail
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache

from .models import Attendee, SessionState

import logging

LOGGER = logging.getLogger(__name__)

AGENT_URL = os.environ.get("AGENT_URL")
INDY_EMAIL_VERIFIER_DID = os.environ.get("INDY_EMAIL_VERIFIER_DID")
STAFF_EMAILS = os.environ.get("STAFF_EMAILS")
CONFERENCE_OPTIONS = os.environ.get("CONFERENCE_OPTIONS")


if not AGENT_URL:
    raise Exception("AGENT_URL is not set")
if not INDY_EMAIL_VERIFIER_DID:
    raise Exception("INDY_EMAIL_VERIFIER_DID is not set")


def index(request):
    template = loader.get_template("index.html")
    return HttpResponse(template.render())


def invite(request):
    response = requests.post(f"{AGENT_URL}/connections/create-invitation")
    response.raise_for_status()
    invite = response.json()
    invitation_url = invite["invitation_url"]
    connection_id = invite["connection_id"]

    didcomm_url = re.sub(r"^https?:\/\/\S*\?", "didcomm://invite?", invitation_url)

    template = loader.get_template("invite.html")

    SessionState.objects.get_or_create(
        connection_id=connection_id, state="invite-created"
    )

    print("\n\n\n")
    print("invite created")
    print("\n\n\n")

    stream = io.BytesIO()
    qr_png = qrcode.make(invitation_url)
    qr_png.save(stream, "PNG")
    qr_png_b64 = base64.b64encode(stream.getvalue()).decode("utf-8")

    return HttpResponse(
        template.render(
            {
                "qr_png": qr_png_b64,
                "didcomm_url": didcomm_url,
                "invitation_url": invitation_url,
                "connection_id": connection_id,
            },
            request,
        )
    )


def state(request, connection_id):
    state = SessionState.objects.get(connection_id=connection_id)
    resp = {"state": state.state}
    try:
        attendee = Attendee.objects.get(connection_id=connection_id)
        resp["email"] = attendee.email
        resp["full_name"] = attendee.full_name
    except:
        pass

    return JsonResponse(resp)


def in_progress(request, connection_id):
    state = SessionState.objects.get(connection_id=connection_id)
    template = loader.get_template("in_progress.html")
    return HttpResponse(
        template.render({"connection_id": connection_id, state: state.state}, request)
    )


def submit_name(request, connection_id):
    if request.method == "GET":
        get_object_or_404(SessionState, connection_id=connection_id)
        template = loader.get_template("submit_name.html")
        conferences = CONFERENCE_OPTIONS.split(",")
        return HttpResponse(
            template.render(
                {"connection_id": connection_id, "conferences": conferences}, request
            )
        )
    elif request.method == "POST":
        full_name = request.POST.get("name")
        conference = request.POST.get("conference")

        attendee = Attendee.objects.get(connection_id=connection_id)
        attendee.full_name = full_name
        attendee.conference = conference

        attendee.save()

        credential_definition_id = cache.get("credential_definition_id")
        assert credential_definition_id is not None

        request_body = {
            "connection_id": str(attendee.connection_id),
            "cred_def_id": credential_definition_id,
            "credential_preview": {
                "@type": "did:sov:BzCbsNYhMrjHiqZDTUASHg;spec/issue-credential/1.0/credential-preview",
                "attributes": [
                    {"name": "email", "value": attendee.email},
                    {"name": "full_name", "value": attendee.full_name},
                    {"name": "conference", "value": attendee.conference},
                    {"name": "time", "value": str(datetime.utcnow())},
                ],
            },
        }

        response = requests.post(
            f"{AGENT_URL}/issue-credential/send-offer", json=request_body
        )
        response.raise_for_status()

        SessionState.objects.filter(connection_id=str(attendee.connection_id)).update(
            state="credential-offer-sent"
        )

        return HttpResponseRedirect(f"/in-progress/{connection_id}")
    else:
        return HttpResponseNotFound("Not found")


@login_required
def backend(request):
    template = loader.get_template("backend.html")
    attendees = Attendee.objects.order_by("-updated_at")
    return HttpResponse(template.render({"attendees": attendees}, request))


# @login_required
# def backend_denied(request):
#     template = loader.get_template("backend_denied.html")
#     attendees = Attendee.objects.filter(approved=False, denied=True)
#     return HttpResponse(template.render({"attendees": attendees}, request))


# @login_required
# def backend_approved(request):
#     template = loader.get_template("backend_approved.html")
#     attendees = Attendee.objects.filter(approved=True)
#     return HttpResponse(template.render({"attendees": attendees}, request))


# def attendees_submit(request):
#     if request.method == "POST":
#         email = request.POST.get("email")
#         full_name = request.POST.get("full_name")
#         approved = request.POST.get("approve")
#         denied = request.POST.get("deny")

#         attendee = Attendee.objects.get(email=email)
#         attendee.full_name = full_name
#         if approved:
#             attendee.approved = True
#         elif denied:
#             attendee.denied = True

#         attendee.save()

#         if approved:
#             # attendance cred def id
#             credential_definition_id = cache.get("credential_definition_id")
#             assert credential_definition_id is not None

#             request_body = {
#                 "connection_id": str(attendee.connection_id),
#                 "cred_def_id": credential_definition_id,
#                 "credential_preview": {
#                     "@type": "did:sov:BzCbsNYhMrjHiqZDTUASHg;spec/issue-credential/1.0/credential-preview",
#                     "attributes": [
#                         {"name": "email", "value": attendee.email},
#                         {"name": "full_name", "value": attendee.full_name},
#                         {"name": "time", "value": str(datetime.utcnow())},
#                     ],
#                 },
#             }

#             response = requests.post(
#                 f"{AGENT_URL}/issue-credential/send-offer", json=request_body
#             )
#             response.raise_for_status()

#             SessionState.objects.filter(
#                 connection_id=str(attendee.connection_id)
#             ).update(state="approved")

#         return HttpResponseRedirect("/backend")

#     return HttpResponseNotFound()


@csrf_exempt
def webhooks(request, topic):

    message = json.loads(request.body)
    LOGGER.info(f"webhook received - topic: {topic} body: {request.body}")

    if topic == "connections" and message["state"] == "request":
        connection_id = message["connection_id"]
        SessionState.objects.filter(connection_id=connection_id).update(
            state="connection-request-received"
        )

    # Handle new invites, send presentation request
    if topic == "connections" and message["state"] == "response":

        #     print("\n\n\n")
        #     print("connection formed")
        #     print("\n\n\n")

        connection_id = message["connection_id"]
        assert connection_id is not None

        SessionState.objects.filter(connection_id=connection_id).update(
            state="connection-formed"
        )

        # ensure that connection response enters dispatch queue
        time.sleep(5)

        LOGGER.info(
            f"Sending presentation request for connection {message['connection_id']}"
        )

        request_body = {
            "connection_id": connection_id,
            "proof_request": {
                "name": "BC Gov Verified Email",
                "version": "1.0.0",
                "requested_predicates": {},
                "requested_attributes": {
                    "email_referent": {
                        "name": "email",
                        "restrictions": [
                            {
                                "issuer_did": INDY_EMAIL_VERIFIER_DID,
                                "schema_name": "verified-email",
                            },
                            {
                                "issuer_did": "85459GxjNySJ8HwTTQ4vq7",
                                "schema_name": "verified_person",
                            },
                        ],
                    }
                },
            },
        }
        response = requests.post(
            f"{AGENT_URL}/present-proof/send-request", json=request_body
        )
        response.raise_for_status()

        print("\n\n\n")
        print("presentation request sent")
        print("\n\n\n")
        SessionState.objects.filter(connection_id=connection_id).update(
            state="request-sent"
        )

        return HttpResponse()

    # TODO: Handle presentation, verify
    if topic == "present_proof" and message["state"] == "presentation_received":
        presentation_exchange_id = message["presentation_exchange_id"]
        assert presentation_exchange_id is not None

        LOGGER.info(
            f"Verifying presentation for presentation id {message['presentation_exchange_id']}"
        )

        response = requests.post(
            f"{AGENT_URL}/present-proof/records/{presentation_exchange_id}/verify-presentation"
        )

        response.raise_for_status()

        LOGGER.info(response.text)

        return HttpResponse()

        # HACK: pretend things are verified because the structure that comes back from mobile agent is invalid

    # Handle verify, save state in db
    if topic == "present_proof" and message["state"] == "verified":
        connection_id = message["connection_id"]
        assert connection_id is not None

        # HACK: we need a better way to pull values out of presentations
        revealed_attrs = message["presentation"]["requested_proof"]["revealed_attrs"]
        for revealed_attr in revealed_attrs.values():
            email = revealed_attr["raw"]

        try:
            attendee = Attendee.objects.get(email=email)
        except Attendee.DoesNotExist:
            attendee = Attendee(email=email)
        attendee.connection_id = connection_id
        # attendee.approved = False
        # attendee.denied = False
        attendee.save()

        # pending_count = Attendee.objects.filter(approved=False, denied=False).count()
        # total_count = Attendee.objects.count()

        # template = loader.get_template("email.html")
        # email_html = template.render(
        #     {"pending_count": pending_count, "total_count": total_count}, request
        # )

        # send_mail(
        #     "New IIWBook Verification Request",
        #     f"Go here to approve or deny this attendee: https://iiwbook.vonx.io/backend",
        #     "IIWBook <noreply@gov.bc.ca>",
        #     STAFF_EMAILS.split(","),
        #     fail_silently=False,
        #     html_message=email_html,
        # )

        SessionState.objects.filter(connection_id=connection_id).update(
            state="presentation-verified"
        )

        return HttpResponse()

    # Handle cred request, issue cred
    if topic == "issue_credential" and message["state"] == "request_received":
        credential_exchange_id = message["credential_exchange_id"]
        connection_id = message["connection_id"]
        assert connection_id is not None

        LOGGER.info(
            "Sending credential issue for credential exchange "
            + f"{credential_exchange_id} and connection {connection_id}"
        )

        attendee = get_object_or_404(Attendee, connection_id=connection_id)
        request_body = {
            "credential_preview": {
                "@type": "did:sov:BzCbsNYhMrjHiqZDTUASHg;spec/issue-credential/1.0/credential-preview",
                "attributes": [
                    {"name": "email", "value": attendee.email},
                    {"name": "full_name", "value": attendee.full_name},
                    {"name": "conference", "value": attendee.conference},
                    {"name": "time", "value": str(datetime.utcnow())},
                ],
            }
        }

        response = requests.post(
            f"{AGENT_URL}/issue-credential/records/{credential_exchange_id}/issue",
            json=request_body,
        )

        response.raise_for_status()

        SessionState.objects.filter(connection_id=connection_id).update(
            state="credential-issued"
        )

        return HttpResponse()

    # # Handle menu request
    # if topic == "get-active-menu":
    #     connection_id = message["connection_id"]
    #     thread_id = message["thread_id"]
    #     LOGGER.info("Returning action menu to %s", connection_id)
    #     message = render_menu(connection_id, thread_id)
    #     if message:
    #         request_body = {"menu": message}
    #         LOGGER.info(f"{AGENT_URL}/connections/{connection_id}/send-menu")
    #         LOGGER.info(request_body)
    #         response = requests.post(
    #             f"{AGENT_URL}/connections/{connection_id}/send-menu", json=request_body
    #         )

    #         response.raise_for_status()

    #     return HttpResponse()

    # # Handle menu perform action
    # if topic == "perform-menu-action":
    #     connection_id = message["connection_id"]
    #     thread_id = message["thread_id"]
    #     action_name = message["action_name"]
    #     action_params = message.get("action_params") or {}
    #     LOGGER.info("Performing action menu action %s %s", action_name, connection_id)
    #     message = perform_menu_action(
    #         action_name, action_params, connection_id, thread_id
    #     )
    #     if message:
    #         request_body = {"menu": message}
    #         LOGGER.info(f"{AGENT_URL}/connections/{connection_id}/send-menu")
    #         LOGGER.info(request_body)
    #         response = requests.post(
    #             f"{AGENT_URL}/connections/{connection_id}/send-menu", json=request_body
    #         )

    #         response.raise_for_status()

    #     return HttpResponse()

    return HttpResponse()


# def is_approved(connection_id: str) -> bool:
#     if USE_TEST_INTROS:
#         return True
#     try:
#         attendee = Attendee.objects.get(approved=True, connection_id=connection_id)
#         return True
#     except Attendee.DoesNotExist:
#         return False


# def render_menu(connection_id: str, thread_id: str) -> dict:
#     """
#     Render the current menu.

#     Args:
#         connection_id: The connection identifier from the requesting message.
#         thread_id: The thread identifier from the requesting message.
#     """

#     message = {
#         "title": "Welcome to IIWBook",
#         "description": "IIWBook facilitates connections between attendees by "
#         + "verifying attendance and distributing connection invitations.",
#         "options": [],
#     }
#     if thread_id:
#         message["~thread"] = {"thid": thread_id}

#     if not is_approved(connection_id):
#         message["options"].append(
#             dict(
#                 name="search-intros",
#                 title="Search introductions",
#                 description="Please submit your email address proof and await approval.",
#                 disabled=True,
#             )
#         )
#         return message

#     search_form = {
#         "title": "Search introductions",
#         "description": "Enter an attendee name below to perform a search.",
#         "submit-label": "Search",
#         "params": [{"name": "query", "title": "Attendee name", "required": True}],
#     }
#     message["options"].append(
#         dict(
#             name="search-intros",
#             title="Search introductions",
#             description="Filter attendee records to make a connection",
#             form=search_form,
#         )
#     )

#     return message


# TEST_INTROS = [
#     {
#         "name": "info;bob",
#         "title": "Bob Terwilliger",
#         "description": "The Krusty the Clown Show",
#     },
#     {"name": "info;ananse", "title": "Kwaku Ananse", "description": "Ghana"},
#     {"name": "info;megatron", "title": "Megatron", "description": "Cybertron"},
# ]
# USE_TEST_INTROS = False


# def find_attendees(query: str):
#     options = []
#     if USE_TEST_INTROS:
#         for row in TEST_INTROS:
#             if (
#                 not query
#                 or query in row["name"].lower()
#                 or query in row["description"].lower()
#             ):
#                 options.append(dict(**row))
#     else:
#         attends = Attendee.objects.filter(
#             approved=True, full_name__contains=query
#         ).order_by("full_name", "email")
#         for record in attends:
#             options.append(
#                 {
#                     "name": f"info;{record.connection_id}",
#                     "title": record.full_name,
#                     "description": record.email,
#                 }
#             )
#     return options


# def get_attendee(attend_id: str):
#     if USE_TEST_INTROS:
#         for row in TEST_INTROS:
#             if row["name"] == f"info;{attend_id}":
#                 return row.copy()
#     else:
#         try:
#             record = Attendee.objects.get(approved=True, connection_id=attend_id)
#             return {
#                 "name": f"info;{record.connection_id}",
#                 "title": record.full_name,
#                 "description": record.email,
#             }
#         except Attendee.DoesNotExist:
#             return


# def perform_menu_action(
#     action_name: str, action_params: dict, connection_id: str, thread_id: str = None
# ) -> dict:
#     """
#     Perform an action defined by the active menu.

#     Args:
#         action_name: The unique name of the action being performed
#         action_params: A collection of parameters for the action
#         thread_id: The thread identifier from the requesting message.
#     """

#     return_option = dict(name="index", title="Back", description="Return to options")

#     if action_name == "index":
#         return render_menu(connection_id, thread_id)

#     elif action_name == "search-intros":
#         LOGGER.info("search intros %s", action_params)
#         if not is_approved(connection_id):
#             return
#         query = action_params.get("query", "").lower()
#         options = find_attendees(query)
#         if not options:
#             return dict(
#                 title="Search results",
#                 description="No attendees were found matching your query.",
#                 options=[return_option],
#             )
#         return dict(
#             title="Search results",
#             description="The following attendees were found matching your query.",
#             options=options,
#         )

#     elif action_name.startswith("info;"):
#         if not is_approved(connection_id):
#             return
#         attend_id = action_name[5:]
#         found = get_attendee(attend_id)
#         if found:
#             request_form = {
#                 "title": "Request an introduction",
#                 "description": "Ask to connect with this user.",
#                 "submit-label": "Send Request",
#                 "params": [dict(name="comments", title="Comments")],
#             }
#             return dict(
#                 title=found["title"],
#                 description=found["description"],
#                 options=[
#                     dict(
#                         name=f"request;{attend_id}",
#                         title="Request an introduction",
#                         description="Ask to connect with this user",
#                         form=request_form,
#                     ),
#                     dict(name="index", title="Back", description="Return to options"),
#                 ],
#             )
#         return dict(
#             title="Attendee not found",
#             description="The attendee could not be located.",
#             options=[return_option],
#         )

#     elif action_name.startswith("request;"):
#         if not is_approved(connection_id):
#             return
#         attend_id = action_name[8:]
#         LOGGER.info("requested intro to %s", attend_id)
#         found = get_attendee(attend_id)
#         if found:
#             if USE_TEST_INTROS:
#                 # invite self
#                 LOGGER.info("test self-introduction")
#                 LOGGER.info(
#                     f"{AGENT_URL}/connections/{connection_id}/start-introduction"
#                 )
#                 response = requests.post(
#                     f"{AGENT_URL}/connections/{connection_id}/start-introduction",
#                     params={
#                         "target_connection_id": connection_id,
#                         "message": action_params.get("comments"),
#                     },
#                 )
#                 response.raise_for_status()
#             else:
#                 # send introduction proposal to user and ..
#                 response = requests.post(
#                     f"{AGENT_URL}/connections/{connection_id}/start-introduction",
#                     params={
#                         "target_connection_id": attend_id,
#                         "message": action_params.get("comments"),
#                     },
#                 )
#                 response.raise_for_status()

#             return dict(
#                 title="Request sent to {}".format(found["title"]),
#                 description="""Your request for an introduction has been received,
#                     and IIWBook will now ask the attendee for a connection
#                     invitation. Once received by IIWBook this invitation will be
#                     forwarded to your agent.""",
#                 options=[
#                     dict(name="index", title="Done", description="Return to options")
#                 ],
#             )

#     else:
#         LOGGER.info(f"Unsupported menu action: {action_name}")
