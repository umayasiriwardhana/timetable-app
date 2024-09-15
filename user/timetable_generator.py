from ortools.sat.python import cp_model
from datetime import datetime, timedelta
from django.db import IntegrityError
from .models import Course, Hall, Lecturer, LecturerAvailability, Timeslot

def generate_timetable(batch_year, degree):
    model = cp_model.CpModel()

    # Get relevant courses
    courses = Course.objects.filter(Batch_Year=batch_year, degree=degree)

    # Get available halls
    halls = Hall.objects.filter(Faculty=degree.Faculty)

    # Get all lecturers teaching these courses
    lecturers = Lecturer.objects.filter(course__in=courses).distinct()

    # Define time slots (assuming 1-hour slots from 9 AM to 5 PM)
    time_slots = [(h, 0) for h in range(9, 17)]  # 9 AM to 4 PM (last slot starts at 4 PM)
    days = range(7)  # Monday to Sunday

    # Decision variables
    course_vars = {}
    for course in courses:
        for day in days:
            for time_slot in time_slots:
                for hall in halls:
                    if hall.type == course.required_hall_type:
                        course_vars[(course.id, day, time_slot, hall.id)] = model.NewBoolVar(f'course_{course.id}_day_{day}_time_{time_slot}_hall_{hall.id}')

    # Constraints

    # 1. Each course should have the correct number of sessions per week
    for course in courses:
        model.Add(sum(course_vars[(course.id, day, time_slot, hall.id)]
                      for day in days
                      for time_slot in time_slots
                      for hall in halls
                      if hall.type == course.required_hall_type) == course.sessions_per_week)

    # 2. No overlapping courses in the same hall
    for day in days:
        for time_slot in time_slots:
            for hall in halls:
                model.Add(sum(course_vars[(course.id, day, time_slot, hall.id)]
                              for course in courses
                              if hall.type == course.required_hall_type) <= 1)

    # 3. Lecturer availability
    for lecturer in lecturers:
        availabilities = LecturerAvailability.objects.filter(lecturer=lecturer)
        for day in days:
            for time_slot in time_slots:
                if not any(availability.day == day and 
                           availability.start_time <= datetime.combine(datetime.min, datetime.min.time()).replace(hour=time_slot[0], minute=time_slot[1]).time() < availability.end_time 
                           for availability in availabilities):
                    for course in courses.filter(lecturer=lecturer):
                        for hall in halls:
                            if hall.type == course.required_hall_type:
                                model.Add(course_vars[(course.id, day, time_slot, hall.id)] == 0)

    # 4. No lecturer should teach more than one course at the same time
    for lecturer in lecturers:
        lecturer_courses = courses.filter(lecturer=lecturer)
        for day in days:
            for time_slot in time_slots:
                model.Add(sum(course_vars[(course.id, day, time_slot, hall.id)]
                              for course in lecturer_courses
                              for hall in halls
                              if hall.type == course.required_hall_type) <= 1)

    # 5. Ensure no course extends beyond 5 PM
    for course in courses:
        for day in days:
            for time_slot in time_slots:
                for hall in halls:
                    if hall.type == course.required_hall_type:
                        if time_slot[0] + course.duration > 17:
                            model.Add(course_vars[(course.id, day, time_slot, hall.id)] == 0)

    # 6. Minimize gaps between classes
    gap_vars = {}
    for day in days:
        for i in range(len(time_slots) - 1):
            gap_vars[(day, i)] = model.NewBoolVar(f'gap_day_{day}_slot_{i}')
            model.Add(sum(course_vars[(course.id, day, time_slots[i], hall.id)]
                          for course in courses
                          for hall in halls
                          if hall.type == course.required_hall_type) == 0).OnlyEnforceIf(gap_vars[(day, i)])
            model.Add(sum(course_vars[(course.id, day, time_slots[i], hall.id)]
                          for course in courses
                          for hall in halls
                          if hall.type == course.required_hall_type) > 0).OnlyEnforceIf(gap_vars[(day, i)].Not())

    # Objective: Minimize the number of gaps
    model.Minimize(sum(gap_vars.values()))

    # Solve the model
    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        # Clear existing timeslots for this batch and degree
        Timeslot.objects.filter(course__Batch_Year=batch_year, course__degree=degree).delete()

        # Create new timeslots based on the solution
        for (course_id, day, time_slot, hall_id), var in course_vars.items():
            if solver.Value(var):
                course = Course.objects.get(id=course_id)
                hall = Hall.objects.get(id=hall_id)
                start_time = datetime.combine(datetime.min, datetime.min.time()).replace(hour=time_slot[0], minute=time_slot[1])
                end_time = start_time + timedelta(hours=course.duration)
                
                # Ensure end_time doesn't exceed 5 PM
                if end_time.time() > datetime.strptime("17:00", "%H:%M").time():
                    end_time = datetime.combine(end_time.date(), datetime.strptime("17:00", "%H:%M").time())
                
                # Check for existing timeslots
                existing_timeslot = Timeslot.objects.filter(
                    day=day,
                    start_time=start_time.time(),
                    end_time=end_time.time(),
                    hall=hall
                ).first()

                if existing_timeslot:
                    print(f"Conflict detected: {existing_timeslot}. Skipping this timeslot.")
                    continue

                try:
                    Timeslot.objects.create(
                        day=day,
                        start_time=start_time.time(),
                        end_time=end_time.time(),
                        hall=hall,
                        course=course
                    )
                except IntegrityError as e:
                    print(f"Failed to create timeslot: {e}. Skipping this timeslot.")

        print(f"Timetable generated successfully for {batch_year} - {degree}")
        print(f"Total gaps: {solver.ObjectiveValue()}")
    else:
        print(f"No solution found for {batch_year} - {degree}")

    return status == cp_model.OPTIMAL or status == cp_model.FEASIBLE