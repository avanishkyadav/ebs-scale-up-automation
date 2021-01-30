import json
import boto3
import time
import os

threshold = int(os.getenv('THRESHOLD_UTILISATION'))
ebs_utilisation_topic_arn = os.getenv('SNS_TOPIC_ARN')

ssm = boto3.client('ssm')
ec2 = boto3.client('ec2')
cw = boto3.client('cloudwatch')

def lambda_handler(event, context):
    if event['InstanceId']=='*':
        reservations = ec2.describe_instances()['Reservations']
        for reservation in reservations:
            for instance in reservation['Instances']:
                initiate_create_alarm(instance)
    else:
        instance = ec2.describe_instances(InstanceIds=[event['InstanceId']])['Reservations'][0]['Instances'][0]
        initiate_create_alarm(instance)
        
def initiate_create_alarm(instance):
    print('===================================================================================================')
    instance_id = instance['InstanceId']
    print('Initiating EBS scaling on "' + instance_id + '".')
    platform='linux'
    try:
        platform = instance['Platform']
    except:
        pass
    print('Platform detected - ' + platform)
    print('Loading cloudwatch agent configuration file.')
    try:
        parameter = json.loads(ssm.get_parameter(
            Name='/CWAgent/'+ platform.capitalize() +'/Disk'
        )['Parameter']['Value'])
    except botocore.exceptions.ClientError as e:
        print('ERROR OCCURED :: ' + e.response['Error']['Message'])
    
    print('Extracting EBS mount points.')   
    if platform == 'linux':
        parameters = {'commands': ["lsblk -o name,fstype,mountpoint| grep 'ext4\|xfs' | awk '{print $3}'"]}
        result = send_ssm_command(instance_id,'AWS-RunShellScript',parameters)
        mountpoints = result['StandardOutputContent'].split('\n')[:-1]
        if result['Status']=='Success' and len(mountpoints)!=0:
            print('Mountpoints extracted :', mountpoints)
            parameter['metrics']['metrics_collected']['disk']['resources'] = mountpoints
        else:
            print('Failed to extract mountpoints.')
            return
    else:
        parameters = {'commands': ["Get-Partition | Select-Object -ExpandProperty DriveLetter"]}
        result = send_ssm_command(instance_id,'AWS-RunPowerShellScript',parameters)
        mountpoints = result['StandardOutputContent'].replace("\r", ":").split('\n')[:-1]
        if result['Status']=='Success' and len(mountpoints)!=0:
            print('Mountpoints extracted :',mountpoints)
            parameter['metrics']['metrics_collected']['LogicalDisk']['resources'] = mountpoints
        else:
            print('Failed to extract mountpoints.')
            return
        
    print('Updating cloudwatch agent configuration file.')
    try:   
        response = ssm.put_parameter(
            Name='/CWAgent/' + platform.capitalize() + '/Disk',
            Value=json.dumps(parameter),
            Type='String',
            Overwrite=True,
            DataType='text'
        )
    except botocore.exceptions.ClientError as e:
        print('ERROR OCCURED :: ' + e.response['Error']['Message'])
            
    print('Installing and configuring CloudWatch agent on "' + instance_id +'".')
    parameters = {'configurationLocation': ['/CWAgent/' + platform.capitalize() + '/Disk']}
    result = send_ssm_command(instance_id,'CloudWatchAgent',parameters)
    if result['Status']=='Success':
        print('CloudWatch agent successfully installed.')
    else:
        print('Failed to install CloudWatch agent.')
        return
    
    if platform == 'linux': 
        print('Extracting disk metadata - {device, fstype, mount} from "'+ instance_id +'".')
        parameters={'commands': ["lsblk -i -o name,fstype,mountpoint|grep 'ext4\|xfs'|sed 's/|-//g' | sed 's/`-//g'|awk -v OFS='\t' '{ print $1,$2,$3}'"]}
        result = send_ssm_command(instance_id, 'AWS-RunShellScript',parameters)
        if result['Status']=='Success':
            print('Metadata successfully extracted.')
            print('Creating CloudWatch alarms on disk utilisation metrics to inititate scale-up automation.')
            i=1
            for disk in result['StandardOutputContent'].split('\n')[:-1]:
                dimensions=[{'Name': 'InstanceId','Value': instance_id}]
                disk_attributes = disk.split('\t')
                dimensions.append({'Name':'device','Value':disk_attributes[0]})
                dimensions.append({'Name':'fstype','Value':disk_attributes[1]})
                dimensions.append({'Name':'path','Value':disk_attributes[2]})
                print('Disk ' + str(i) + ' : {'+disk_attributes[0]+','+disk_attributes[1]+','+disk_attributes[2]+'}')
                i+=1
                create_alarm(instance_id,'disk_used_percent',dimensions,disk_attributes[0],threshold,"GreaterThanThreshold")
        else:
            print('Failed to extract metadata.')
            return
    else:
        print('Creating CloudWatch alarms on disk utilisation metrics to inititate scale-up automation.')
        dimensions={}
        i=1
        for mountpoint in parameter['metrics']['metrics_collected']['LogicalDisk']['resources']:
            dimensions=[{'Name':'InstanceId','Value':instance_id}]
            dimensions.append({'Name':'instance','Value':mountpoint})
            dimensions.append({'Name':'objectname','Value':'LogicalDisk'})
            print('Disk ' + str(i) + ' : {'+mountpoint+'}')
            i+=1
            create_alarm(instance_id,'LogicalDisk % Free Space',dimensions,mountpoint,100-threshold,"LessThanThreshold")

def send_ssm_command(instance_id, document, parameters):
    try:
        response = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName=document,
            Parameters=parameters
        )
        while True:
            time.sleep(1)
            result = ssm.list_commands(
                CommandId=response['Command']['CommandId'],
                InstanceId=instance_id
            )['Commands'][0]
            if result['Status']=='InProgress' or result['Status']=='Pending':
                pass
            elif result['Status']=='Success':
                if document!='CloudWatchAgent':
                    result = ssm.get_command_invocation(
                        CommandId=response['Command']['CommandId'],
                        InstanceId=instance_id
                    )
                return result
            else:
                return result
    except:
        print('Failed to send command. Make sure ssm agent is installed on instance and appropriate role is attached to ec2 instance.')
        return {'Status':'Failed', 'StandardOutputContent':''}
        
def create_alarm(instance_id,metric_name,dimensions,volume,threshold,comparison):
    print('Creating "'+"EBSUtilisationExceededAlarm-"+instance_id+"-"+volume+'" CloudWatch alarm.')
    cw.put_metric_alarm(
        AlarmActions=[
            ebs_utilisation_topic_arn,
        ],
        ComparisonOperator=comparison,
        EvaluationPeriods=1,
        DatapointsToAlarm=1,
        Threshold=threshold,
        AlarmName="EBSUtilisationExceededAlarm-"+instance_id+"-"+volume,
        MetricName=metric_name,
        Namespace='CWAgent',
        Statistic='Average',
        Dimensions=dimensions,
        Period=60,
    )
    print('Alarm created successfully.')