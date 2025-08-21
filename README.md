# AWS SSM Session Log Parser Lambda

A Python Lambda function to parse unstructured AWS SSM Session Manager logs from S3 into clean, structured JSON, ready for ingestion into any SIEM or log analysis platform.

## The Problem

AWS Systems Manager (SSM) Session Manager is an excellent tool for secure instance access, and its session logging feature is crucial for auditing. However, the raw logs sent to S3 are essentially terminal dumps, complete with ANSI escape codes, control characters, and line editing sequences (like backspaces).

When ingested directly into a SIEM (like Elastic SIEM), each line of a command's output becomes a separate, disjointed log entry. This makes it incredibly difficult to answer a simple but critical question: **"What commands did a specific user run during a session, and what was the output?"**

This Lambda function solves that problem.

## Solution Overview

This function acts as an automated processing pipeline for your SSM logs. It is triggered whenever a new raw log file is uploaded to your S3 bucket. It then performs the following actions:

1.  **Reads the raw log file.**
2.  **Cleans the terminal output**, removing ANSI codes and correctly interpreting backspaces and carriage returns to reconstruct the true commands and output.
3.  **Parses the entire session**, intelligently identifying shell prompts to separate individual commands from their multi-line output.
4.  **Enriches the data** by extracting critical metadata from the log file's path and content.
5.  **Writes a new JSON file** to a `/processed_logs/` directory in the same S3 bucket. Each line in the new file is a self-contained JSON object representing a single command and its context.

The result is a clean, structured log file that your SIEM can easily ingest, with each command and its full output represented as a single, coherent event.

## Features

* **Structured Output:** Converts raw terminal streams into structured JSON Lines format.
* **Intelligent Parsing:** Accurately separates shell prompts, commands, and multi-line outputs.
* **Advanced Cleaning:** Handles ANSI escape codes, control characters, backspaces (`\b`), and carriage returns (`\r`) to reconstruct the true state of the terminal.
* **Rich Metadata Extraction:**
    * **AWS Account ID:** From the S3 object key.
    * **User:** From the log filename.
    * **Session ID:** From the log filename.
    * **Instance ID:** From the log content.
    * **Session Start Time:** From the log content.
* **Robust & Resilient:** Gracefully handles different shell prompts (sh, bash, etc.) and connection methods (CLI vs. AWS Console).

## S3 Bucket Setup

The Lambda function is designed to work with a specific S3 bucket structure for both input and output.

* **Source Path:** The function expects raw SSM logs to be delivered to a path that includes the AWS Account ID.
    * Example: `s3://your-ssm-log-bucket/123456789012/some-user@example.com-sessionid123.log`
* **Destination Path:** The function will write the processed JSON files back to the **same bucket** under a dedicated prefix.
    * Example: `s3://your-ssm-log-bucket/processed_logs/123456789012/some-user@example.com-sessionid123.json`

Your SIEM's S3 input should be configured to only read from the `processed_logs/` prefix to avoid ingesting the raw, unprocessed data.

## The Instance ID Trick

A key piece of metadata is the EC2 instance ID where the session occurred. Since this isn't in the log by default, we need to add it. This can be done by modifying the shell profile on your EC2 instances to print the instance ID at the beginning of every session.

The Lambda parser is built to handle two methods, ensuring compatibility across different AMIs like Amazon Linux and Bottlerocket.

### Method 1: For Standard AMIs (e.g., Amazon Linux)

For instances with the `ec2-metadata` tool, you can add a script to `/etc/profile.d/` that runs:

```bash
# /etc/profile.d/ssm-metadata.sh
ec2-metadata --instance-id
```

The Lambda parser looks for the output `instance-id: i-...`

### Method 2: For Minimalist AMIs (e.g., Bottlerocket)

For instances without `ec2-metadata`, like Bottlerocket, you can use `curl` to query the EC2 Metadata Service (IMDSv2) directly. This is the recommended approach as it standardizes the output.

Add the following command to a script in `/etc/profile.d/`:

```bash
# /etc/profile.d/ssm-metadata.sh
TOKEN=`curl -s -X PUT "http://169.254.169.24/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60"` && \
IID=`curl -s http://169.254.169.254/latest/meta-data/instance-id -H "X-aws-ec2-metadata-token: $TOKEN"` && \
echo "instance-id: $IID"
```

This command securely fetches a token, gets the instance ID, and prints it in the exact same format as the `ec2-metadata` tool, allowing the same Lambda parser to work for all instances.

## Deployment & Configuration

### IAM Permissions

The Lambda function's execution role requires the following IAM policy. **Remember to replace `your-ssm-log-bucket-name` with your actual bucket name.**

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "S3PermissionsForSSMLogProcessing",
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject"
            ],
            "Resource": "arn:aws:s3:::your-ssm-log-bucket-name/*"
        },
        {
            "Sid": "CloudWatchLogsPermissions",
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "arn:aws:logs:*:*:*"
        }
    ]
}
```

## Final Output Schema

The processed file will be in JSON Lines format (one JSON object per line). Each object will have the following structure:

```json
{
    "session_start_time": "2025-06-26 13:50:01+03:00",
    "instance_id": "i-1234567890abcdef0",
    "session_id": "sessionid1234567",
    "user": "some-user@example.com",
    "aws_account_id": "123456789012",
    "command": "sudo hack-this-machine",
    "output": "sudo: hack-this-machine: command not found"
}
