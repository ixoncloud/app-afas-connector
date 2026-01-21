from datetime import datetime
import json
import base64
import requests
from urllib.parse import quote

from ixoncdkingress.cbc.context import CbcContext
from typing import List, Dict, Union


# ------------------------------------
# Public functions
# ------------------------------------

@CbcContext.expose
def get_files(context: CbcContext) -> List[Dict[str, str]]:
    return _get_files_from_afas(context)


@CbcContext.expose
def download_file(context: CbcContext, file_id: str, file_name: str):
    webhook_url = context.config.get('webhook_url')

    if (webhook_url):
        now = datetime.now()
        iso_format = now.strftime("%Y-%m-%dT%H:%M:%S")
        requests.post(webhook_url,
                      data={'agent_or_asset': context.agent_or_asset.name,
                            'file_id': file_id, 'file_name': file_name,
                            'utc_time': iso_format})

    return _download_file_from_afas(context, file_id, file_name)


# ------------------------------------
# AFAS functions
# ------------------------------------

def _get_files_from_afas(context: CbcContext) -> List[Dict[str, str]]:
    error_conditions = _check_error_conditions(context)
    if error_conditions:
        return error_conditions

    token = context.config.get('token')
    environment_id = context.config.get('environment_id')
    dossier_per_project_connector = context.config.get(
        'dossier_per_project_connector')
    files_per_dossier_connector = context.config.get(
        'files_per_dossier_connector')

    project_id = context.config.get(
        'project_id_custom_field_id')

    project_id = context.agent_or_asset.custom_properties.get(
        project_id)

    afas_token = _get_encoded_afas_token(token)
    headers = {'Authorization': afas_token}

    url_dossier_per_project_connector = _build_connector_url(
        environment_id, dossier_per_project_connector)
    response = requests.get(url_dossier_per_project_connector, headers=headers)

    if response.status_code == 200:
        response = response.json()

        rows = response.get('rows')

        filtered_rows = _get_filtered_rows(
            rows, 'Project', project_id)
        dossier_items = [row.get('Dossieritem') for row in filtered_rows]

        url_files_per_dossier_connector = _build_connector_url(
            environment_id, files_per_dossier_connector)
        response = requests.get(
            url_files_per_dossier_connector, headers=headers)

        if response.status_code == 200:
            response = response.json()
            rows = response.get('rows')

            filtered_rows = _get_filtered_rows_in(
                rows, 'Dossieritem', dossier_items)
            files_list = _get_files_list(filtered_rows)
            return files_list

    return {'error': 'Something went wrong'}


def _download_file_from_afas(context: CbcContext, file_id: str, file_name: str) -> Dict[str, str]:
    error_conditions = _check_error_conditions(context)
    if error_conditions:
        return error_conditions

    token = context.config.get('token')
    environment_id = context.config.get('environment_id')

    afas_token = _get_encoded_afas_token(token)
    url = _build_afas_file_download_url(environment_id, file_id, file_name)

    headers = {'Authorization': afas_token}

    try:
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 200:
            try:
                content = json.loads(response.content)
                file_name = content['filename']
                mime_type = content['mimetype']
                filedata_base64 = content['filedata']
                return {'file_name': file_name, 'pdf_file': f"data:{mime_type};base64,{filedata_base64}"}
            except (json.JSONDecodeError, KeyError) as e:
                return {'error': f'Invalid response from AFAS: {str(e)}'}
        else:
            error_msg = f'AFAS returned status {response.status_code}'

            if response.status_code == 404:
                return {'error': f'File not found. The file "{file_name}" may have been moved, renamed, or deleted in AFAS.'}
            elif response.status_code == 401:
                return {'error': 'Authentication failed. Please check your AFAS token.'}
            elif response.status_code == 403:
                return {'error': 'Access forbidden. You may not have permission to access this file.'}
            elif response.status_code == 500:
                # AFAS sometimes returns 500 with 404 HTML content - check if it's actually a 404
                response_text = response.text if hasattr(
                    response, 'text') else str(response.content[:500])
                if '404' in response_text or 'not found' in response_text.lower():
                    return {'error': f'File not found (404). The file "{file_name}" may not exist in AFAS, or the filename may not match exactly what is stored. Please verify the filename in AFAS matches: {repr(file_name)}'}
                else:
                    return {'error': f'AFAS server error (500). This may indicate an issue with the filename encoding or AFAS server configuration. Original filename: "{file_name}". Try checking if the filename contains characters that AFAS cannot handle.'}
            else:
                return {'error': f'Failed to download file: {error_msg}'}

    except requests.exceptions.Timeout:
        return {'error': 'Request to AFAS timed out. Please try again later.'}
    except requests.exceptions.RequestException as e:
        return {'error': f'Network error: {str(e)}'}

# ------------------------------------
# AFAS helper functions
# ------------------------------------


def _check_error_conditions(context: CbcContext) -> Union[List
                                                          [Dict[str, str]],
                                                          None]:
    token = context.config.get('token')
    if not token:
        return {'error': 'No token found'}

    environment_id = context.config.get('environment_id')
    if not environment_id:
        return {'error': 'No environment id found'}

    dossier_per_project_connector = context.config.get(
        'dossier_per_project_connector')
    if not dossier_per_project_connector:
        return {'error': 'No dossier_per_project_connector found'}

    files_per_dossier_connector = context.config.get(
        'files_per_dossier_connector')
    if not files_per_dossier_connector:
        return {'error': 'No files_per_dossier_connector found'}

    project_id = context.config.get(
        'project_id_custom_field_id')
    project_id = context.agent_or_asset.custom_properties.get(
        project_id)
    if not project_id:
        return {'error': 'No serial number found'}

    return None


def _get_encoded_afas_token(token: str) -> str:
    encoded_token = base64.b64encode(token.encode()).decode()
    return f"AfasToken {encoded_token}"


# https://help.afas.nl/help/NL/SE/App_Cnr_Rest_Api.htm#o106273
# Workaround for now default limit is 100 rows per request and we need all rows
# I think you will need an extreme amount of machines before a request timeout (max 15 minutes)
def _build_connector_url(environment_id: str, connector: str) -> str:
    return f"https://{environment_id}.rest.afas.online/ProfitRestServices/connectors/{connector}?skip=-1&take=-1"


def _encode_filename_for_afas(file_name: str) -> str:
    """
    Encode filename according to AFAS FileConnector requirements.

    Documentation: https://help.afas.nl/help/NL/SE/App_Cnr_Rest_FileCn.htm#o87737

    AFAS documentation specifies:
    - For special characters listed in their table, use underscore encoding: + -> _2B, / -> _2F, etc.
    - For other characters (spaces, UTF-8 chars like ë), use standard percent-encoding: %20, %C3%AB
    - Example: "Gabriëls 123456.pdf" -> "Gabri%C3%ABls%20123456.pdf" (space uses %20, ë uses %C3%AB)
    - But + in filename should be encoded as _2B according to the table

    AFAS encoding table:
    / -> _2F, # -> _23, & -> _26, : -> _3A, ? -> _3F, * -> _2A, < -> _3C, > -> _3E,
    % -> _25, + -> _2B, ~ -> _7E, - -> _2D, @ -> _40, ! -> _21, $ -> _24, _ -> _5F, ' -> _27
    """
    if not file_name:
        raise ValueError("File name cannot be empty")

    # Ensure we're working with a proper string
    if isinstance(file_name, bytes):
        file_name = file_name.decode('utf-8', errors='replace')

    # Strip any leading/trailing whitespace
    file_name = file_name.strip()

    # AFAS uses a hybrid encoding approach per their documentation:
    # 1. Special characters from the table use underscore encoding: + -> _2B, / -> _2F, etc.
    # 2. Everything else (spaces, UTF-8 chars, alphanumeric, etc.) uses standard percent-encoding
    #
    # Example from AFAS docs: "Gabriëls 123456.pdf" -> "Gabri%C3%ABls%20123456.pdf"
    # - Space is encoded as %20 (percent encoding, NOT underscore)
    # - ë is encoded as %C3%AB (percent encoding for UTF-8)
    # - Special chars from table (like +) would be _2B (underscore encoding)

    # Map of special characters to AFAS underscore encoding (from AFAS documentation table)
    # Note: Space is NOT in this table, so it will use percent encoding (%20)
    afas_special_chars = {
        '/': '_2F',
        '#': '_23',
        '&': '_26',
        ':': '_3A',
        '?': '_3F',
        '*': '_2A',
        '<': '_3C',
        '>': '_3E',
        '%': '_25',
        '+': '_2B',  # Key fix: + must be _2B, not %2B
        '~': '_7E',
        '-': '_2D',
        '@': '_40',
        '!': '_21',
        '$': '_24',
        '_': '_5F',
        "'": '_27',
    }

    # Build encoded string character by character
    result = []
    for char in file_name:
        if char in afas_special_chars:
            # Use AFAS underscore encoding for special characters from the table
            result.append(afas_special_chars[char])
        else:
            # Use standard URL percent-encoding for everything else
            # This handles:
            # - Spaces -> %20 (as shown in AFAS example)
            # - UTF-8 chars like ë -> %C3%AB (as shown in AFAS example)
            # - Preserves alphanumeric and safe characters like . (period)
            # quote() with default safe parameter preserves alphanumeric and ._-
            encoded_char = quote(char)
            result.append(encoded_char)

    return ''.join(result)


def _build_afas_file_download_url(
        environment_id: str, file_id: str, file_name: str) -> str:
    # https://help.afas.nl/help/NL/SE/App_Cnr_Rest_FileCn.htm#o87737
    # Beware that comma's in the file name do not work in the url even when encoded, AFAS bug?
    #
    # AFAS documentation states:
    # - Filename must be the original name from GetConnector
    # - Special characters from the table use underscore encoding (e.g., + -> _2B)
    # - Spaces and UTF-8 characters use percent-encoding (e.g., space -> %20, ë -> %C3%AB)
    # - Example: "Gabriëls 123456.pdf" -> "Gabri%C3%ABls%20123456.pdf"

    encoded_file_name = _encode_filename_for_afas(file_name)
    url = f"https://{environment_id}.rest.afas.online/ProfitRestServices/fileconnector/{file_id}/{encoded_file_name}"
    return url


def _get_filtered_rows(rows: List[Dict[str, str]],
                       key: str, value: str) -> List[Dict[str, str]]:
    return [row for row in rows if row.get(key) == value]


def _get_filtered_rows_in(rows: List[Dict[str, str]],
                          key: str, values: List[str]) -> List[Dict[str, str]]:
    return [row for row in rows if row.get(key) in values]


def _get_files_list(filtered_rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [{'name': row.get('Naam'), 'id': row.get('Bijlage')} for row in filtered_rows]
