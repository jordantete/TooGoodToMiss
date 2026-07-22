import boto3, pytz, random
from typing import Optional, Tuple, List
from datetime import datetime, timedelta
from app.common.utils import Utils
from app.common.logger import LOGGER
from app.common.constants import SCHEDULE_RULE_NAME_PREFIX, WEEKDAY_MAP

class Scheduler:
    MORNING_WINDOW = ((10, 12), (10, 20))  # Morning: 10:00-12:00 with 10-20 mins delay
    AFTERNOON_WINDOW = ((12, 19), (2, 5))  # Afternoon: 12:00-19:00 with 2-5 mins delay

    def __init__(self, ):
        """Initialize the Scheduler."""
        aws_account_id = Utils.get_environment_variable("AWS_ACCOUNT_ID")
        aws_region = Utils.get_environment_variable("DEFAULT_AWS_REGION")
        self.monitoring_lambda_arn = f"arn:aws:lambda:{aws_region}:{aws_account_id}:function:too-good-to-miss-monitoring"
        self.events_client: boto3.client = boto3.client('events')
        self.lambda_client: boto3.client = boto3.client('lambda') 

    def _is_in_cooldown(self) -> Tuple[bool, Optional[float]]:
        """Check if the function is in a cooldown state and return the remaining time in seconds if active."""
        try:
            response = self.lambda_client.get_function_configuration(FunctionName=self.monitoring_lambda_arn)
            env_vars = response.get('Environment', {}).get('Variables', {})
            cooldown_end_time_str = env_vars.get('COOLDOWN_END_TIME')
            
            if not cooldown_end_time_str:
                LOGGER.info("No cooldown end time set. Cooldown is not active.")
                return False, None
            
            cooldown_end_time = datetime.fromisoformat(cooldown_end_time_str).replace(tzinfo=pytz.utc)
            now_utc = datetime.now(pytz.utc)
            LOGGER.info(f"cooldown_end_time: {cooldown_end_time}")
            LOGGER.info(f"now_utc: {now_utc}")

            if now_utc < cooldown_end_time:
                remaining_time = (cooldown_end_time - now_utc).total_seconds()
                LOGGER.info(f"Cooldown is active. Remaining time: {remaining_time:.0f} seconds.")
                return True, remaining_time

            LOGGER.info("Cooldown period has expired. Cooldown is not active.")
            return False, None

        except Exception as e:
            LOGGER.error(f"Unexpected error while checking cooldown status: {e}")
            return False, None

    def _convert_datetime_to_cron_expression(
        self, 
        dt: datetime
    ) -> str:
        """Convert a datetime object to a CRON expression."""
        dt_utc = dt.astimezone(pytz.utc)
        return f"cron({dt_utc.minute} {dt_utc.hour} {dt_utc.day} {dt_utc.month} ? {dt_utc.year})"

    def _list_scheduled_rules(self) -> List[dict]:
        """Fetch all rules with the defined prefix."""
        response = self.events_client.list_rules(NamePrefix=SCHEDULE_RULE_NAME_PREFIX)
        return response.get('Rules', [])
    
    def _is_future_rule(
        self, 
        rule: dict, 
        now_utc: datetime
    ) -> bool:
        """Determine if the rule is scheduled for a future date."""
        rule_datetime = self._extract_datetime_from_rule(rule['Name'])
        return rule_datetime and rule_datetime > now_utc

    def _extract_datetime_from_rule(
        self, 
        rule_name: str
    ) -> Optional[datetime]:
        """Extract datetime from a rule name."""
        try:
            rule_datetime_str = rule_name.split('_')[-1]
            return datetime.strptime(rule_datetime_str, '%Y%m%d%H%M').replace(tzinfo=pytz.utc)

        except ValueError:
            return None

    def _delete_past_rule(
        self, 
        rule: dict
    ) -> None:
        """Delete a rule and its associated targets."""
        try:
            targets = self.events_client.list_targets_by_rule(Rule=rule['Name'])
            target_ids = [target['Id'] for target in targets.get('Targets', [])]

            if target_ids:
                self.events_client.remove_targets(Rule=rule['Name'], Ids=target_ids)
                LOGGER.info(f"Removed targets for rule: {rule['Name']}")

            self.events_client.delete_rule(Name=rule['Name'])
            LOGGER.info(f"Deleted past due rule: {rule['Name']}")

        except Exception as e:
            LOGGER.error(f"Failed to delete rule {rule['Name']}: {e}")

    def _has_future_invocation(self) -> bool:
        """Check if a future invocation is already scheduled."""
        now_utc = datetime.now(pytz.utc)

        rules = self._list_scheduled_rules()
        future_rules = [rule for rule in rules if self._is_future_rule(rule, now_utc)]

        if future_rules:
            LOGGER.info(f"Future invocation already exists: {future_rules[0]['Name']}")
            return True

        past_rules = [rule for rule in rules if not self._is_future_rule(rule, now_utc)]
        for rule in past_rules:
            self._delete_past_rule(rule)
        
        LOGGER.info("No future invocation exists.")
        return False

    def _get_time_window(
        self, 
        current_hour: int
    ) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """Determine the active time window based on the current hour."""
        for window_name, ((start_hour, end_hour), delay_range) in {
            'morning': self.MORNING_WINDOW,
            'afternoon': self.AFTERNOON_WINDOW,
        }.items():
            if start_hour <= current_hour < end_hour:
                LOGGER.info(f"Current time falls within the {window_name} window.")
                return (start_hour, end_hour), delay_range
        return None

    def _calculate_next_invocation_time(self) -> Optional[datetime]:
        """Calculate the next invocation time based on time windows."""
        now = datetime.now(pytz.utc)
        if WEEKDAY_MAP[now.weekday()] == 'Sunday':
            LOGGER.info("Today is Sunday - no next invocation scheduled.")
            return None

        time_window = self._get_time_window(now.hour)

        if time_window:
            _, delay_range = time_window
            delay = timedelta(minutes=random.randint(*delay_range))
            next_invocation = now + delay
            LOGGER.info(f"Next invocation calculated at {next_invocation} with delay of {delay}.")
            return next_invocation

        LOGGER.info("No active time window found - no next invocation scheduled.")
        return None

    def _create_rule(
        self, 
        rule_name: str, 
        cron_expression: str
    ) -> None:
        """Create a new CloudWatch Events rule and target."""
        try:
            self.events_client.put_rule(Name=rule_name, ScheduleExpression=cron_expression, State='ENABLED')
            self.events_client.put_targets(Rule=rule_name, Targets=[{'Id': '1', 'Arn': self.monitoring_lambda_arn}])
            LOGGER.info(f"Created rule {rule_name} with CRON expression {cron_expression}")

        except Exception as e:
            LOGGER.error(f"Failed to create rule {rule_name}: {e}")
    
    def activate_cooldown(
        self,
        cooldown_minutes: int = 30
    ) -> None:
        """Activate cooldown by updating Lambda environment variables."""
        try:
            LOGGER.info("Triggering cooldown due to anti-bot detection.")
            new_env_vars = {"COOLDOWN_END_TIME": (datetime.now(pytz.utc) + timedelta(minutes=cooldown_minutes)).isoformat()}
            Utils.update_lambda_env_vars(self.monitoring_lambda_arn, new_env_vars)
            LOGGER.info("Cooldown successfully activated.")

        except Exception as e:
            LOGGER.error(f"Failed to activate cooldown: {e}")
    
    def remove_cooldown(self) -> None:
        """Remove the cooldown by clearing the COOLDOWN_END_TIME in the Lambda environment variables."""
        try:
            LOGGER.info("Removing cooldown and waking up the bot.")            
            new_env_vars = {"COOLDOWN_END_TIME": ""}
            Utils.update_lambda_env_vars(self.monitoring_lambda_arn, new_env_vars)
            LOGGER.info("Cooldown successfully removed. The bot is now active.")
            
        except Exception as e:
            LOGGER.error(f"Failed to remove cooldown: {e}")
    
    def is_bot_paused(self) -> bool:
        """Check if the bot is paused."""
        is_in_cooldown, _ = self._is_in_cooldown()
        return is_in_cooldown

    def schedule_next_invocation(self) -> None:
        """Schedule the next invocation based on current conditions."""
        is_in_cooldown, _ = self._is_in_cooldown()

        if is_in_cooldown:
            LOGGER.info("Skipping schedule due to active cooldown.")
            return

        if self._has_future_invocation():
            LOGGER.info("A future invocation is already scheduled. No new rule created.")
            return

        next_invocation_time = self._calculate_next_invocation_time()

        if next_invocation_time:
            cron_expression = self._convert_datetime_to_cron_expression(next_invocation_time)
            rule_name = f"{SCHEDULE_RULE_NAME_PREFIX}{next_invocation_time.strftime('%Y%m%d%H%M')}"
            self._create_rule(rule_name, cron_expression)
        else:
            LOGGER.info("No next invocation scheduled due to off-peak hours or Sunday.")