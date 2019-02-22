from flask import Blueprint, request, Flask

import json
import flask
import requests
import ntpath
from datetime import date, datetime

blueprint = Blueprint("manifests", __name__)

import boto3
session = boto3.Session(region_name="us-east-1")
s3 = session.resource("s3")

def get_folder_name_from_user_info(user_info):
    return "user-" + str(user_info["user_id"])

def does_the_user_have_read_access_on_at_least_one_project(project_access_dict):
    privileges = list(project_access_dict.values())
    
    if len(privileges) == 0:
        return False

    for auth_set in privileges:
        if "read" in auth_set and "read-storage" in auth_set:
            return True

    return False

def is_the_user_permitted_to_use_this_service(access_token):
    """
    This function returns (access_token, full_user_info_response_from_fence)
    """
    headers = {
        "Accept" : "application/json",
        "Content-Type": "application/json",
    }

    cookies = {
        "access_token" : access_token
    }
    
    FENCE_USER_INFO_URL = flask.current_app.config.get("FENCE_USER_INFO_URL")
    r = requests.get(FENCE_USER_INFO_URL, headers=headers, cookies=cookies)
    
    try:
        json = r.json()
    except:
        return False, None

    project_access_dict = json["project_access"]

    return does_the_user_have_read_access_on_at_least_one_project(project_access_dict), json

def is_valid_manifest(manifest_json):
    valid_keys = set(["object_id" , "subject_id"])
    error_msg = "Manifest format is invalid. Please POST a list of key-value pairs, like [{'k' : v}, ...] Valid keys are: " + " ".join(valid_keys)
    if type(manifest_json) != list:
        return False, error_msg

    if len(manifest_json) == 0:
        return True, ""

    for record in manifest_json:
        record_keys = record.keys()
        if not set(record_keys).issubset(valid_keys):
            return False, error_msg

    return True, ""

def generate_unique_manifest_filename(folder_name, manifest_bucket_name):
    timestamp = datetime.now().isoformat()
    users_existing_manifest_files = list_files_in_bucket(manifest_bucket_name, folder_name)
    filename = generate_unique_filename_with_timestamp_and_increment(timestamp, users_existing_manifest_files)
    return filename

def generate_unique_filename_with_timestamp_and_increment(timestamp, users_existing_manifest_files):
    filename_without_extension = "manifest-" + timestamp.replace(":", "-")
    extension = ".json"

    filename = filename_without_extension + extension
    i = 1
    while filename in users_existing_manifest_files:
        filename = filename_without_extension + extension
        if filename in users_existing_manifest_files:
            filename = filename_without_extension + "-" + str(i) + extension
        i += 1

    return filename

def list_files_in_bucket(bucket_name, folder):
    rv = []
    bucket = s3.Bucket(bucket_name)

    for object_summary in bucket.objects.filter(Prefix=folder + "/"):
        rv.append(ntpath.basename(object_summary.key))
    
    return rv

def get_file_contents(bucket_name, folder, filename):
    client = boto3.client("s3")
    obj = client.get_object(Bucket=bucket_name, Key=folder + "/" + filename)
    as_bytes = obj["Body"].read()
    as_string = as_bytes.decode("utf-8")
    return as_string.replace("'", "\"")

@blueprint.route("/", methods=["GET"])
def get_manifests():
    """
    Returns a list of filenames corresponding to the user's manifests
    """
    access_token = request.cookies.get("access_token")
    if access_token is None:
        json_to_return = { "error" : "Please log in." }
        return flask.jsonify(json_to_return), 403

    auth_successful, user_info = is_the_user_permitted_to_use_this_service(access_token)
    if not auth_successful:
        json_to_return = { "error" : "You must have read access on at least one project in order to use this feature." }
        return flask.jsonify(json_to_return), 403
    
    folder_name = get_folder_name_from_user_info(user_info)

    MANIFEST_BUCKET_NAME = flask.current_app.config.get("MANIFEST_BUCKET_NAME")
    json_to_return = {
        "manifests" : list_files_in_bucket(MANIFEST_BUCKET_NAME, folder_name)
    }

    return flask.jsonify(json_to_return), 200

@blueprint.route("/file/<file_name>", methods=["GET"])
def get_manifest_file(file_name):
    """
    Returns the requested manifest file from the user's folder.
    """
    if not file_name.endswith("json"):
        json_to_return = { "error" : "Incorrect usage. You can only use this pathway to request files of type JSON." }
        return flask.jsonify(json_to_return), 403

    access_token = request.cookies.get("access_token")
    if access_token is None:
        json_to_return = { "error" : "Please log in." }
        return flask.jsonify(json_to_return), 403

    auth_successful, user_info = is_the_user_permitted_to_use_this_service(access_token)
    if not auth_successful:
        json_to_return = { "error" : "You must have read access on at least one project in order to use this feature." }
        return flask.jsonify(json_to_return), 403
    
    folder_name = get_folder_name_from_user_info(user_info)

    MANIFEST_BUCKET_NAME = flask.current_app.config.get("MANIFEST_BUCKET_NAME")
    json_to_return = {
        "body" : get_file_contents(MANIFEST_BUCKET_NAME, folder_name, file_name)
    }

    print(json_to_return)

    return flask.jsonify(json_to_return), 200

def add_manifest_to_bucket(user_info, manifest_json):
    folder_name = get_folder_name_from_user_info(user_info)

    MANIFEST_BUCKET_NAME = flask.current_app.config.get("MANIFEST_BUCKET_NAME")
    filename = generate_unique_manifest_filename(folder_name, MANIFEST_BUCKET_NAME)
    manifest_as_bytes = str.encode(str(flask.request.json))
    filepath_in_bucket = folder_name + "/" + filename

    obj = s3.Object(MANIFEST_BUCKET_NAME, filepath_in_bucket)
    response = obj.put(Body=manifest_as_bytes)
    return filename

@blueprint.route("/", methods=["PUT", "POST"])
def put_manifest():
    """
    Add manifest to s3 bucket
    """

    access_token = request.cookies.get("access_token")
    if access_token is None:
        json_to_return = { "error" : "Please log in." }
        return flask.jsonify(json_to_return), 403

    auth_successful, user_info = is_the_user_permitted_to_use_this_service(access_token)
    if not auth_successful:
        json_to_return = { "error" : "You must have read access on at least one project in order to use this feature." }
        return flask.jsonify(json_to_return), 403

    if not flask.request.json:
        return flask.jsonify({"error" : "Please provide valid JSON."}), 400
    
    manifest_json = flask.request.json
    is_valid, err = is_valid_manifest(manifest_json)
    if not is_valid:
        return flask.jsonify({"error" : err}), 400

    filename = add_manifest_to_bucket(user_info, manifest_json)

    ret = {
        "filename": filename,
    }

    return flask.jsonify(ret), 200