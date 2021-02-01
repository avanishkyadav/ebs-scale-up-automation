from aws_cdk import (
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_sns as sns,
    aws_sns_subscriptions as sns_sub,
    aws_ssm as ssm,
    core
)
import json

class EbsScaleUpAutomationStack(core.Stack):

    def __init__(self, scope: core.Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        theshold_util= core.CfnParameter(
            self, "Threshold-Utilisation-Percent",
            type="String",
            allowed_pattern='^[1-9]$|^[1-9][0-9]$|^(100)$',
            description="(Required) Utilisation at which EBS scale out should happen. [1-100]."
        )
        
        target_util = core.CfnParameter(
            self, "Target-Utilisation-Percent",
            type="String",
            allowed_pattern='^[1-9]$|^[1-9][0-9]$|^(100)$',
            description="(Required) Target utilisation which must be achieved after scale out happen. [1-100]."
        )
        
        enable_sns = core.CfnParameter(
            self, "Enable-Notification",
            type="String",
            allowed_values=["yes","no"],
            default="no",
            description='Select "yes" if you want to be notified about scaling events.'
        )
        
        sns_arn = core.CfnParameter(
            self, "Notification-Topic-Arn",
            type="String",
            description='If selected \"Yes\". Topic arn to which notification will be sent.'
        )
        
        ce_agent_doc={}
        with open('ssm/cloudwatch-agent-installation-document.json', 'r') as f:
             cw_agent_doc = json.load(f)
        
        cloudwatch_ssm_document = ssm.CfnDocument(
            self, 'CloudWatchInstallationDocument',
            content=cw_agent_doc,
            document_type='Command',
            name='CloudWatchAgent'
        )
        
        with open('ssm/cloudwatch-config-windows.json', 'r') as f:
            cw_config_windows = f.read()
        
        windows_cwagent_paramter = ssm.CfnParameter(
            self, 'WindowsCWAgentConfig',
            type="String",
            value=cw_config_windows,
            data_type='text',
            name='/CWAgent/Windows/Disk'
        )
        
        with open('ssm/cloudwatch-config-linux.json', 'r') as f:
            cw_config_linux = f.read()
        
        linux_cwagent_paramter = ssm.CfnParameter(
            self, 'LinuxCWAgentConfig',
            type="String",
            value=cw_config_linux,
            data_type='text',
            name='/CWAgent/Linux/Disk'
        )
        
        ebs_util_exceeded_topic = sns.Topic(
            self, 'EBSUtilisationExceededTopic',
            topic_name='ebs-utilisation-exceeded-topic'
        )
        
        create_metric_alarm_policy = iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(
                    sid='WriteCloudWatchLogs',
                    actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                    effect=iam.Effect.ALLOW,
                    resources=['*']
                ),
                iam.PolicyStatement(
                    sid="describeEC2Instance",
                    actions=["ec2:DescribeInstances"],
                    effect=iam.Effect.ALLOW,
                    resources=["*"]
                ),
                iam.PolicyStatement(
                    sid="ssmParameterAccess",
                    actions=["ssm:GetParameter","ssm:PutParameter"],
                    effect=iam.Effect.ALLOW,
                    resources= ['*']    
                ),
                iam.PolicyStatement(
                    sid="ssmCommandInvocation",
                    actions=["ssm:SendCommand","ssm:GetCommandInvocation"],
                    effect=iam.Effect.ALLOW,
                    resources= ['*']    
                ),
                iam.PolicyStatement(
                    sid="cwCreateAlarm",
                    actions=["cloudwatch:PutMetricAlarm"],
                    effect=iam.Effect.ALLOW,
                    resources= ['*']    
                )
            ]
        )
        
        create_metric_alarm_lambda = _lambda.Function(
            self, 'CreateEBSMetricAlarmFunction',
            handler='create-alarm.lambda_handler',
            function_name='create-ebs-metric-alarm-function',
            code=_lambda.Code.from_asset(path='lambda/'),
            runtime=_lambda.Runtime.PYTHON_3_7,
            timeout=core.Duration.seconds(900),
            memory_size=128,
            role=iam.Role(
                self, 'CreateEBSMetricAlarmRole',
                assumed_by=iam.ServicePrincipal('lambda.amazonaws.com'),
                role_name='create-ebs-metric-alarm-role',
                inline_policies={
                    'create-ebs-metric-alarm-policy': create_metric_alarm_policy
                }
            ),
            environment={
                'THRESHOLD_UTILISATION': theshold_util.value_as_string,
                'UTIL_EXCEEDED_SNS_TOPIC_ARN':ebs_util_exceeded_topic.topic_arn
            }
        )
        
        scale_ebs_policy = iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(
                    sid='WriteCloudWatchLogs',
                    actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                    effect=iam.Effect.ALLOW,
                    resources=['*']
                ),
                iam.PolicyStatement(
                    sid="EC2AndEBS",
                    actions=["ec2:DescribeVolumes","ec2:ModifyVolume","ec2:DescribeInstanceAttribute"],
                    effect=iam.Effect.ALLOW,
                    resources=["*"]
                ),
                iam.PolicyStatement(
                    sid="ssmCommandInvocation",
                    actions=["ssm:SendCommand","ssm:GetCommandInvocation"],
                    effect=iam.Effect.ALLOW,
                    resources= ['*']    
                ),
                iam.PolicyStatement(
                    sid="snsPublishNotification",
                    actions=["sns:Publish"],
                    effect=iam.Effect.ALLOW,
                    resources=["*"]    
                )
            ]
        )
        
        ebs_scaling_lambda = _lambda.Function(
            self, 'EBSScalingFunction',
            handler='scale-ebs.lambda_handler',
            function_name='scale-ebs-function',
            code=_lambda.Code.from_asset(path='lambda/'),
            runtime=_lambda.Runtime.PYTHON_3_7,
            timeout=core.Duration.seconds(300),
            memory_size=128,
            role=iam.Role(
                self, 'ScaleEBSRole',
                assumed_by=iam.ServicePrincipal('lambda.amazonaws.com'),
                role_name='scale-ebs-role',
                inline_policies={
                    'scale-ebs-policy': scale_ebs_policy
                }
            ),
            environment={
                'THRESHOLD_UTILISATION': theshold_util.value_as_string,
                'DESIRED_UTILISATION': target_util.value_as_string,
                'ENABLE_SNS':enable_sns.value_as_string,
                'SNS_NOTIFICATION_TOPIC_ARN':sns_arn.value_as_string
            }
        )
        
        ebs_util_exceeded_topic.add_subscription(sns_sub.LambdaSubscription(ebs_scaling_lambda))

        
