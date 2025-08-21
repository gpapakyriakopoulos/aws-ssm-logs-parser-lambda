import boto3
import re
import json
import os
import urllib.parse

s3 = boto3.client('s3')

def clean_terminal_output(data):
    """
    Cleans a raw terminal log stream by removing ANSI escape codes and
    correctly processing backspace and carriage return characters.
    """
    # This more aggressive regex removes most ANSI escape and Operating System Command sequences.
    ansi_and_osc_regex = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -/]*[@-~]|\x1B].*?(\x07|\x1B\\)|(\x07)')
    data = ansi_and_osc_regex.sub('', data)

    # Process lines to handle backspace (\b) and carriage return (\r)
    clean_lines = []
    for line in data.split('\n'):
        line_content = ''
        for part in line.split('\r'):
            line_content = part + line_content[len(part):]

        final_chars = []
        for char in line_content:
            if char == '\b' or char == '\x08':
                if final_chars:
                    final_chars.pop()
            else:
                final_chars.append(char)
        clean_lines.append("".join(final_chars))
    
    return "\n".join(clean_lines)

def add_log_entry(logs_list, command, output_list, session_time, instance_id, session_id, user, account_id):
    """
    Helper function to process and append a command and its output to the log list,
    performing necessary checks and formatting.
    """
    processed_command = command.strip()
    # Ensure the command is not empty and not the instance-id output line.
    if processed_command and not processed_command.startswith("instance-id:"):
        logs_list.append({
            "session_start_time": session_time,
            "instance_id": instance_id,
            "session_id": session_id,
            "user": user,
            "aws_account_id": account_id,
            "command": processed_command,
            "output": "\n".join(output_list).strip(),
        })

def lambda_handler(event, context):
    """
    This Lambda function is triggered by an S3 event. It reads a raw AWS SSM
    Session Manager log file, cleans and parses it into structured JSON,
    and writes the result back to the same S3 bucket under a 'processed_logs/' path.
    """
    bucket = event['Records'][0]['s3']['bucket']['name']
    encoded_key = event['Records'][0]['s3']['object']['key']
    key = urllib.parse.unquote_plus(encoded_key)

    if key.startswith('processed_logs/'):
        print(f"Skipping already processed file: {key}")
        return {'statusCode': 200, 'body': json.dumps('File is in processed_logs directory. Skipping.')}

    # --- Extract user, session ID, and account ID from the key ---
    try:
        # The account ID is the first directory in the key path.
        key_parts = key.split('/')
        account_id = key_parts[0] if len(key_parts) > 1 and key_parts[0].isdigit() else "unknown"

        original_filename = os.path.basename(key)
        # Split from the right on the last hyphen to separate user and session ID
        base, _ = os.path.splitext(original_filename)
        parts = base.rsplit('-', 1)
        if len(parts) == 2:
            user, session_id = parts
        else:
            # Fallback if the format is unexpected (e.g., no hyphen)
            user = parts[0] if parts else "unknown"
            session_id = "unknown"
    except Exception:
        user = "unknown"
        session_id = "unknown"
        account_id = "unknown"

    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        log_content = response['Body'].read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"Error getting object {key} from bucket {bucket}. Error: {e}")
        raise e

    session_start_time = "unknown"
    time_match = re.search(r"^Script started on (.+)", log_content, re.MULTILINE)
    if time_match:
        full_timestamp = time_match.group(1).strip()
        session_start_time = full_timestamp.split('[', 1)[0].strip()

    cleaned_log = clean_terminal_output(log_content)
    
    instance_id = "unknown"
    instance_id_match = re.search(r"instance-id: (i-[0-9a-f]+)", cleaned_log)
    if instance_id_match:
        instance_id = instance_id_match.group(1)

    # --- Updated Parsing Logic ---
    prompt_regex = r"(sh-\d\.\d(?:\(via ssm-agent-session\))?\$|\S+@\S+:\S+[#$]|\[.+?@.+?\s.+?\][#$]|\$)\s?"
    start_script_regex = r"^Script started on"
    end_script_regex = r"^Script done on"

    structured_logs = []
    current_command = None
    current_output = []

    for line in cleaned_log.splitlines():
        if re.search(start_script_regex, line) or re.search(end_script_regex, line):
            continue

        prompt_match = re.search(prompt_regex, line)
        
        if prompt_match:
            if current_command is not None:
                add_log_entry(structured_logs, current_command, current_output, session_start_time, instance_id, session_id, user, account_id)
            
            current_command = line[prompt_match.end():]
            current_output = []
        elif current_command is not None:
            current_output.append(line)

    # Add the last captured command
    if current_command is not None:
        add_log_entry(structured_logs, current_command, current_output, session_start_time, instance_id, session_id, user, account_id)

    if not structured_logs:
        print(f"No structured logs were generated for {key}.")
        return {'statusCode': 200, 'body': json.dumps('No commands found in log.')}

    output_content = "\n".join([json.dumps(log) for log in structured_logs])
    
    original_path = os.path.dirname(key)
    new_filename = os.path.splitext(original_filename)[0] + '.json'
    new_key = os.path.join('processed_logs', original_path, new_filename) if original_path else os.path.join('processed_logs', new_filename)

    try:
        s3.put_object(Bucket=bucket, Key=new_key, Body=output_content, ContentType='application/json')
        print(f"Successfully processed {key} and wrote to {new_key}")
    except Exception as e:
        print(f"Error writing processed log to {new_key}. Error: {e}")
        raise e

    return {
        'statusCode': 200,
        'body': json.dumps(f'Successfully processed {key} into {new_key}')
    }