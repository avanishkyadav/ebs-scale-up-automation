import json
import os
import boto3
import time
import math
import botocore

sns_enabled = os.getenv('ENABLE_SNS').lower()
sns_arn = os.getenv('SNS_NOTIFICATION_TOPIC_ARN')
req_utilisation = os.getenv('DESIRED_UTILISATION')
threshold = os.getenv('THRESHOLD_UTILISATION')

ec2 = boto3.client('ec2')
ssm = boto3.client('ssm')
sns= boto3.client('sns')

def find_volume_id(params):   
    instance_id = params['instance_id']
    
    # Instance is windows
    if params['drive']:
        parameters = {
            'commands': [
                    "$a = get-partition -DriveLetter " + params['drive'] + " | Select-Object -ExpandProperty DiskNumber",
                    "$b = iex 'get-disk -Number $($a)' | Select-Object -ExpandProperty SerialNumber",
                    "$c = $b -split '_'",
                    "$c[0]"
            ]
        }
        result = send_ssm_command(instance_id,'AWS-RunPowerShellScript',parameters)
        if result['Status']=='Success':
            return (result['StandardOutputContent'][0:3] + '-' + result['StandardOutputContent'][3:]).rstrip()
        else:
            return
        
    #if instance is nitro based    
    elif 'nvme' in params['device']:
        parameters = {
            'commands': ["lsblk -o NAME,SERIAL | grep '" + params['device'][0:7] + "' | awk '{ print $2 }'"]
        }
        result = send_ssm_command(instance_id,'AWS-RunShellScript',parameters)
        if result['Status']=='Success':
            return (result['StandardOutputContent'][0:3] + '-' + result['StandardOutputContent'][3:]).rstrip()
        else:
            return
        
    #if instace is non-nvme    
    else:
        mapping_letter=''
        if 'xvd' in params['device']:
            mapping_letter=params['device'][3]
        elif 'sd' in params['device']:
            mapping_letter=params['device'][2]
        else:
            return
        
        print('Extracting block device mapping.')
        volumes = ec2.describe_instance_attribute(
            Attribute='blockDeviceMapping',
            InstanceId=instance_id
        )['BlockDeviceMappings']
        
        for volume in volumes:
            if 'xvd'+mapping_letter in volume['DeviceName'] or 'sd'+mapping_letter in volume['DeviceName']:
                return volume['Ebs']['VolumeId']
        return
 
def send_ssm_command(instance_id, document, parameters):
    try:
        response = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName=document,
            Parameters=parameters
        )
        while True:
            time.sleep(3)
            result = ssm.get_command_invocation(
                        CommandId=response['Command']['CommandId'],
                        InstanceId=instance_id
                    )
            if result['Status']=='InProgress' or result['Status']=='Pending':
                pass
            else:
                return result
    except:
        print('Failed to send command. Make sure ssm agent is installed on instance and appropriate role is attached to ec2 instance.')
        return {'Status':'Failed', 'StandardOutputContent':''}


def lambda_handler(event, context):
    alarm_name = json.loads(event['Records'][0]['Sns']['Message'])['AlarmName']
    instance_id = alarm_name.split(':')[1]
    print('"'+ alarm_name +'" CloudWatch alarm triggered.')
    sns_notification_msg = '"'+ alarm_name +'" CloudWatch alarm triggered.'
    print('Starting EBS scaling for "'+ instance_id +'".')
    sns_notification_msg += '\nStarting EBS scaling for "'+ instance_id +'".'
    
    print("Extracting metric metadata.")
    params = {'mount_point':'','instance_id':'','device':'','file_system':'','drive':''}
    for element in json.loads(event['Records'][0]['Sns']['Message'])['Trigger']['Dimensions']:
        if element['name']=='path':
            params['mount_point']=element['value']
        elif element['name']=='InstanceId':
            params['instance_id']=element['value']
        elif element['name']=='device':
            params['device']=element['value']
        elif element['name']=='fstype':
            params['file_system']=element['value']
        elif element['name']=='instance':
            params['drive']=element['value'][0]
    print('Metadata extracted -')
    print(json.dumps(params))
    
    print('Finding EBS volume id.')
    sns_notification_msg += '\nFinding EBS volume id.'
    volume_id = find_volume_id(params)
    if volume_id == None:
        print("Failed to find volume id.\nFailed to execute EBS Scaling.")
        sns_notification_msg += "\nFailed to find volume id.\nFailed to execute EBS Scaling."
        publish_sns(sns_notification_msg)
        return
    else:
        print("VolumeId : " + volume_id)
        sns_notification_msg += "\nVolumeId : " + volume_id
    
    print("Extracting EBS volume current size.")
    current_ebs_size=0
    try:
        response = ec2.describe_volumes(
            VolumeIds=[volume_id]
        )
        current_ebs_size = response['Volumes'][0]['Size']
    except botocore.exceptions.ClientError as e:
        print('ERROR OCCURED :: ' + e.response['Error']['Message'])
        sns_notification_msg += "\nERROR OCCURED :: " + e.response['Error']['Message']
        publish_sns(sns_notification_msg)
        return
    print("Current volume size : " + str(current_ebs_size) + 'GB')
    sns_notification_msg += "\nCurrent volume size : " + str(current_ebs_size) + 'GB'
    req_ebs_size = int(math.ceil(int(threshold)*current_ebs_size)/int(req_utilisation))
    print("Target volume size  : " + str(req_ebs_size) + 'GB')
    sns_notification_msg += "\nTarget volume size  : " + str(req_ebs_size) + 'GB'
    
    print("Modifying volume.")
    sns_notification_msg += "\nModifying volume."

    try:
        response = ec2.modify_volume(
            VolumeId=volume_id,
            Size=int(req_ebs_size)
        )
        print('Successfully modified volume.')
        sns_notification_msg += "\nSuccessfully modified volume."
    except botocore.exceptions.ClientError as e:
        print('Failed to modify volume.\nERROR OCCURED :: ' + e.response['Error']['Message'])
        sns_notification_msg += '\nFailed to modify volume.\nERROR OCCURED :: ' + e.response['Error']['Message']
        publish_sns(sns_notification_msg)
        return

    partition_number = ''
    dev_wout_partition=''
    if len(params['device'])==9 or len(params['device'])==5 or(len(params['device'])==4 and 'sd' in params['device']):
        partition_number=params['device'][-1]
        
    if 'nvme' in params['device']:
        dev_wout_partition=params['device'][0:7]
    elif 'xvd' in params['device']:
        dev_wout_partition=params['device'][0:4]
    else:
        dev_wout_partition=params['device'][0:3]
        
    if params['drive']:
        print('Starting to extend partition "'+params['drive']+'" at OS level.')
        sns_notification_msg += '\nStarting to extend partition "'+params['drive']+'" at OS level.'
        parameters = {
            'commands': [
                "Start-Sleep -s 60",
                "$a = Get-PartitionSupportedSize -DriveLetter " + params['drive'] + " | Select-Object -ExpandProperty SizeMax",
                "$b = [math]::floor($a/1024)",
                "iex \"Resize-Partition -DriveLetter " + params['drive'] + " -Size $($b)KB\""
            ]
        }
        result = send_ssm_command(instance_id,'AWS-RunPowerShellScript',parameters)
        if result['Status']=='Success':
            print('EBS scaling completed successfully.')
            sns_notification_msg += '\nEBS scaling completed successfully.'
            
        else:
            print('Failed to complete EBS scaling at OS level.')
            sns_notification_msg += '\nFailed to complete EBS scaling at OS level.'

    else:
        print('Starting to extend partition "'+params['device']+'" at OS level.')
        sns_notification_msg += '\nStarting to extend partition "'+params['device']+'" at OS level.'
        parameters = {
            'commands': [
                "sleep 60",
                "if [ \"" + partition_number + "\" != \"\" ];then growpart /dev/" + dev_wout_partition + " " + partition_number + ";fi;",
                "if [ \"" + params['file_system'] + "\" = \"ext4\" ];then resize2fs /dev/" + params['device'] + "; else xfs_growfs -d " + params['mount_point'] + "; fi"
            ]
        }
        result = send_ssm_command(instance_id,'AWS-RunShellScript',parameters)
        if result['Status']=='Success':
            print('EBS scaling completed successfully.')
            sns_notification_msg += '\nEBS scaling completed successfully.'
        else:
            print('Failed to complete EBS scaling at OS level.')
            sns_notification_msg += '\nFailed to complete EBS scaling at OS level.'
    
    publish_sns(sns_notification_msg)        
    

def publish_sns(sns_notification_msg):
    if sns_enabled=='yes':
        response = sns.publish(
           TopicArn=sns_arn,
           Message=sns_notification_msg
        )