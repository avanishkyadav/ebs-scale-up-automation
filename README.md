
# Scheduled Scaling of RDS

Currently, AWS doesn't provide any autoscaling capability for RDS instances except for RDS Aurora. But Scheduled scaling can be implemented for RDS using Amazon EventBridge and AWS Lambda. This solutions have two stacks one for vertical scaling and other for horizontal scaling and these can be deployed independently. 

### Vertical Scaling 
Scheduled vertical scaling changes the RDS instance type, provisioned iops, storage etc. 

### Horizontal Scaling 
Scheduled horizontal scaling adds or removes the read replicas associated with RDS instances.

## Architecture
![Architecture Diagram](architecture/rds-scheduled-scaling.png)

### How It Works
Each of these stacks creates two separate Amazon EventBridge rules. One triggers lambda to scale-up the resources and other triggers lambda to scale it down.

1. A scheduled EventBridge rule triggers lambda.
2. Lambda then scans each RDS instances one by one and checks for the tag `SCHEDULED_SCALING`:`ENABLED`. If this tag is found then lambda looks for tags `SCALE_DOWN_INSTANCE_CLASS`,`SCALE_UP_INSTANCE_CLASS` for vertical-scaling and `SCALE_IN_REPLICA_COUNT`,`SCALE_OUT_REPLICA_COUNT` for horizontal-scaling.
3. Lambda then make API calls to modify instances, create or delete read replica depending on which EventBridge rule is triggered and type of scaling happening.
4. SNS notification is sent containing the summary of scaling operation.

## Installation
This solution can be build either by deploying cdk stack from your environment or by using cloudformation template already synthesized.

### CDK Stack
To install using cdk stack

1. Clone this repository to your local machine.

    ```
    $ git clone https://github.com/avanishkyadav/ebs-scale-up-automation.git
    ```
   
2.  Install cdk if you don’t have it already installed.
    
    ```
    $ npm install -g aws-cdk
    ```

3.  If this is first time you are using cdk then, run cdk bootstrap.
    
    ```
    $ cdk bootstrap
    ```

4.  Make sure you in root directory.
    
    ```
    $ cd ebs-scale-up-automation
    ```
   
5.  Activate virtual environment.
    
    ```
    $ source .venv/bin/activate
    ```

6.  Install any dependencies.
    
    ```
    $ pip install -r requirements.txt
    ```

7.  List stacks. This will list out the stacks present in the project. In this case the only stack will be `ebs-scale-up-automation`.
    
    ```
    $ cdk ls
    ```

8.  Synthesize cloudformation templates. The templates will be generated in `cdk.out/` directory.

    ```
    $ cdk synth 
    ```

9.  Deploying the stacks to create resources. List of [parameters](#stack-parameters) for each stacks.
    
    ```
    $ cdk deploy <stack-name> --parameters "<stack-name>:<parameter-name>=<parameter-value>"
    # e.g, cdk deploy ebs-scale-up-automation --parameters "ebs-scale-up-automation:TargetUtilisationPercent=70" --parameters "ebs-scale-up-automation:ThresholdUtilisationPercent=90"
    ```

### CloudFormation Template 
To install using cloudformation template

1. Upload file `ebs-scale-up.zip` to a bucket. Tow download file click [here](https://automation-assets-avaya.s3.ap-south-1.amazonaws.com/lambda-archives/ebs-scale-up.zip).
2. To launch CloudFormation stack, click  [here](https://console.aws.amazon.com/cloudformation/home?#/stacks/new?stackName=ebs-scale-up-automation&templateURL=https://automation-assets-avaya.s3.ap-south-1.amazonaws.com/cftemplates/ebs-scale-up-automation.template.json).
3. Fill out parameters value. List of [parameters](#stack-parameters) for each stacks.

## Resources
Following table contains list of the primary resources created.
| Name | Type | Description |
| ----------- | ----------- | ----------- |
| create-ebs-metric-alarm-function | Lambda Function | This function initiate EBS scale-up automation on EC2 by launching CloudWatch agent to create utilisation metric and creating CloudWatch alarm on metric. |
| scale-ebs-function | Lambda Function | This function scale up the EBS by first modifying volume and then expanding disk at OS level. |
| /CWAgent/Windows/Disk | System Manager Parameter Store | This stores cloudwatch agent configuration of disk for windows machine.  |
| /CWAgent/Linux/Disk | System Manager Parameter Store | This stores cloudwatch agent configuration of disk for linux machine. |
| CloudWatchAgent | System Manager Document | This is a composite SSM document which installs and configure CloudWatch agent on EC2. |

##  RDS Configuration
To implement scheduled scaling on an RDS create following tags.

## Stack Parameters
Prameters required for stack creation.
| Parameter Name | Description |
| ----------- | ----------- |
| BucketName |	Bucket where `ebs-scale-up.zip` file is uploaded. (Remark - Applicable only if installing with CloudFormation Template.) |
| KeyName | S3 Key name of the file `ebs-scale-up.zip`.  (Remark - Applicable only if installing with CloudFormation Template. Default - `ebs-scale-up.zip`) |
| EnableNotification | Publish scaling summary to NotificationTopicArn. |
| NotificationTopicArn | SNS topic arn to which notification will be published. |
| TargetUtilisationPercent | Target utilisation which must be achieved after scale out happen. [1-100]. |
| ThresholdUtilisationPercent | Time at which scale-out of read replicas will take place. |

**Note -** 
- Every type of RDS instances have different constraints on number of replicas that can be created, rds instnace class allowd. So make sure a rds can be scaled to a particular instance class and replica count before creating rds scaling tags.
- Scale time parameters like `ScaleInTime`, `ScaleOutTime` etc should be in UTC timezone in the format `minute hour` and there should be no leading zero in `minute` or `hour` e.g. `14:05 UTC` will be filled out as `5 14`.
- This solution is not made to work on RDS Aurora.
- Whenever either horizontal or vertical scaling is triggered, the database enters `modifying` state and it takes around 15 minutes before it comes back to `available` state. During this period no further modification can be made to database. It is recommended  that if you are creating both rds-scheduled-horizontal-scaling and rds-scheduled-vertical-scaling stack then there should be atleast 15 minutes gap between `ScaleUpTime` and `ScaleOutTime` otherwise if both scaling are triggered at same one of them will make the changes to database sending it to `modifying` state and other scaling will skip over it because it is not in `available` state.
