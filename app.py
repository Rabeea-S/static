from shutil import rmtree
from json import loads, dumps
from contextlib import suppress
from urllib3 import PoolManager
from os import getenv, environ, scandir, remove
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

http = PoolManager(retries=0)
api_key = getenv("API_ACCESS_KEY", "")

def handler(event, context):
    """
    (1) Fetches app code, (2) compiles app code, (3) runs main(user_inputs), (4) returns outputs
    :param event: {inputs, url, headers}
        - inputs: user inputs
        - url: url to fetch app code
        - headers: optional headers for url
    :param context: lambda context
    :throws: lambda handles all exceptions and tracebacks
    :return: {statusCode, body} on success or {errorMessage, errorType, stackTrace, requestId} on error
    """
    try:
        # Log the incoming event
        logger.info("Received event: %s", event)

        # (0) Run app code in a clean environment
        with NewEnv():
            # (1) Fetch app code
            inputs, url, headers = (
                event["inputs"],
                event["url"],
                event.get("headers", {"Authorization": f"Token {api_key}"}),
            )

            logger.info("Fetching app code from URL: %s with headers: %s", url, headers)
            response = http.request("GET", url, headers=headers, timeout=60)
            code = response.data.decode("utf-8")
            logger.info("Response status: %s, Headers: %s", response.status, response.headers)
            assert response.status == 200, f"Failed to fetch app code: {str(code)}\n"

            # (2) Execute app code
            g = {}  # globals without context
            exec(code, g)

            # (3) Run main(user_inputs) if defined
            assert "main" in g, "'def main(inputs):' is not defined"
            outputs = g["main"](inputs)

            # (4) Return outputs and parse to json
            return {"statusCode": 200, "body": to_json(outputs)}
    except Exception as e:
        logger.error("Error occurred: %s", str(e), exc_info=True)
        return {
            "statusCode": 400,
            "body": {
                "errorMessage": str(e),
                "errorType": type(e).__name__,
                "stackTrace": [],
                "requestId": context.aws_request_id,
            },
        }

class NewEnv:
    """
    Context manager to exec() code in a clean environment
    Cleans up environment variables and files in /tmp
    Cuz AWS Lambda will share environments between consecutive invocations
    """

    def __init__(self):
        self.orig_environ = dict(environ)

    def __enter__(self):
        # Clear all environment variables
        environ.clear()
        # Keep only environment variables that do not contain specified patterns (case-insensitive)
        exclude_patterns = ["AWS", "KEY"]
        environ.update(
            {
                key: value
                for key, value in self.orig_environ.items()
                if all(
                    pattern.upper() not in key.upper() for pattern in exclude_patterns
                )
            }
        )
        logger.info("Environment variables after clearing and updating: %s", environ)

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore original environment variables
        environ.clear()
        environ.update(self.orig_environ)

        # Delete all files and folders in /tmp
        for item in scandir("/tmp"):
            if item.is_file():
                remove(item.path)
            elif item.is_dir():
                rmtree(item.path, ignore_errors=True)
        logger.info("Cleaned up /tmp directory and restored environment variables")

def to_json(unsafe_json):
    """
    Converts unsafe json to safe json object without any json serialization errors
    :param unsafe_json: unsafe dictionary with any invalid json types
    :return: valid json object
    """

    # (1) Parse invalid types
    def set_default(obj):
        ### [set, np.ndarray, pd.core.series.Series] --> list ###
        lists = (set,)
        # Add list types from modules if installed
        with suppress(ImportError):
            from numpy import ndarray

            lists += (ndarray,)

        with suppress(ImportError):
            from pandas import Series

            lists += (Series,)

        # Convert to list
        if isinstance(obj, lists):
            return list(obj)

        # Fallback, convert to string
        ### any --> str ###
        try:
            return str(obj)
        except Exception as e:
            raise TypeError from e  # Raise error if still invalid

    # (2) Return safe json
    return loads(dumps(unsafe_json, default=set_default))
