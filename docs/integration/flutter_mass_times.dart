import 'package:intl/intl.dart';

/// Represents a single mass time entry from the bulletin extraction system.
class MassTime {
  final String day;
  final int time; // 24hr format: 900 = 9:00am, 1630 = 4:30pm
  final DateTime? massDate; // null for weekly, specific date for holidays
  final String? language;
  final String? notes;

  MassTime({
    required this.day,
    required this.time,
    this.massDate,
    this.language,
    this.notes,
  });

  factory MassTime.fromJson(Map<String, dynamic> json) {
    return MassTime(
      day: json['day'] as String,
      time: json['time'] as int,
      massDate: json['mass_date'] != null
          ? DateTime.parse(json['mass_date'] as String)
          : null,
      language: json['language'] as String?,
      notes: json['notes'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
        'day': day,
        'time': time,
        'mass_date': massDate?.toIso8601String().split('T')[0],
        'language': language,
        'notes': notes,
      };

  /// Whether this is a regular weekly mass (vs a holiday/special mass).
  bool get isWeeklyMass => massDate == null;

  /// Whether this is a holiday or special occasion mass.
  bool get isHolidayMass => massDate != null;

  /// Returns the time formatted as 12-hour string (e.g., "9:00 AM").
  String get formattedTime {
    final hour = time ~/ 100;
    final minute = time % 100;
    final dt = DateTime(2000, 1, 1, hour == 24 ? 0 : hour, minute);
    return DateFormat.jm().format(dt);
  }

  /// Returns the mass date formatted (e.g., "Dec 24").
  String? get formattedDate {
    if (massDate == null) return null;
    return DateFormat.MMMd().format(massDate!);
  }

  /// Display label combining notes and date for holiday masses.
  String get displayLabel {
    if (isWeeklyMass) {
      return notes ?? '';
    }
    final parts = <String>[];
    if (notes != null) parts.add(notes!);
    if (massDate != null) parts.add(formattedDate!);
    return parts.join(' - ');
  }
}

/// Service for filtering and querying mass times.
class MassTimeService {
  final List<MassTime> _allMasses;

  MassTimeService(this._allMasses);

  /// Get all masses that should be displayed (weekly + upcoming holidays).
  List<MassTime> getVisibleMasses({
    DateTime? today,
    int holidayWindowDays = 7,
  }) {
    final now = today ?? DateTime.now();
    final todayDate = DateTime(now.year, now.month, now.day);
    final windowEnd = todayDate.add(Duration(days: holidayWindowDays));

    return _allMasses.where((mass) {
      if (mass.isWeeklyMass) return true;

      // Holiday mass: show if within the display window
      final massDate = mass.massDate!;
      return !massDate.isBefore(todayDate) && !massDate.isAfter(windowEnd);
    }).toList();
  }

  /// Get masses for a specific calendar date.
  /// Returns both regular weekly masses for that day of week
  /// AND any holiday masses on that exact date.
  List<MassTime> getMassesForDate(DateTime date) {
    final targetDate = DateTime(date.year, date.month, date.day);
    final dayOfWeek = DateFormat.EEEE().format(date); // "Sunday", "Monday", etc.

    return _allMasses.where((mass) {
      // Holiday mass on this exact date
      if (mass.massDate != null) {
        final massDate = DateTime(
          mass.massDate!.year,
          mass.massDate!.month,
          mass.massDate!.day,
        );
        return massDate == targetDate;
      }

      // Regular weekly mass on this day of week
      return mass.day == dayOfWeek;
    }).toList()
      ..sort((a, b) => a.time.compareTo(b.time));
  }

  /// Get only regular weekly masses.
  List<MassTime> get weeklyMasses =>
      _allMasses.where((m) => m.isWeeklyMass).toList();

  /// Get only holiday/special masses.
  List<MassTime> get holidayMasses =>
      _allMasses.where((m) => m.isHolidayMass).toList();

  /// Get upcoming holiday masses within the next N days.
  List<MassTime> getUpcomingHolidayMasses({
    DateTime? today,
    int days = 14,
  }) {
    final now = today ?? DateTime.now();
    final todayDate = DateTime(now.year, now.month, now.day);
    final windowEnd = todayDate.add(Duration(days: days));

    return _allMasses
        .where((mass) {
          if (mass.massDate == null) return false;
          final massDate = mass.massDate!;
          return !massDate.isBefore(todayDate) && !massDate.isAfter(windowEnd);
        })
        .toList()
      ..sort((a, b) => a.massDate!.compareTo(b.massDate!));
  }

  /// Group masses by day of week (for weekly schedule display).
  Map<String, List<MassTime>> groupWeeklyByDay() {
    final grouped = <String, List<MassTime>>{};
    const dayOrder = [
      'Sunday',
      'Monday',
      'Tuesday',
      'Wednesday',
      'Thursday',
      'Friday',
      'Saturday'
    ];

    for (final day in dayOrder) {
      final dayMasses = weeklyMasses
          .where((m) => m.day == day)
          .toList()
        ..sort((a, b) => a.time.compareTo(b.time));
      if (dayMasses.isNotEmpty) {
        grouped[day] = dayMasses;
      }
    }
    return grouped;
  }
}

// Example usage and widget code below

/*
// Parsing from JSON:
final jsonData = [
  {"day": "Sunday", "time": 900, "mass_date": null, "language": null, "notes": null},
  {"day": "Sunday", "time": 1100, "mass_date": null, "language": "Spanish", "notes": null},
  {"day": "Tuesday", "time": 1600, "mass_date": "2025-12-24", "language": null, "notes": "Christmas Eve Vigil"},
];

final masses = jsonData.map((j) => MassTime.fromJson(j)).toList();
final service = MassTimeService(masses);

// Get masses for a specific date:
final todayMasses = service.getMassesForDate(DateTime.now());

// Get upcoming holiday masses:
final holidays = service.getUpcomingHolidayMasses(days: 14);

// Group weekly masses by day:
final byDay = service.groupWeeklyByDay();
*/

// ---------------------------------------------------------------------------
// Example Flutter Widget
// ---------------------------------------------------------------------------

/*
import 'package:flutter/material.dart';

class MassScheduleWidget extends StatelessWidget {
  final List<MassTime> masses;

  const MassScheduleWidget({super.key, required this.masses});

  @override
  Widget build(BuildContext context) {
    final service = MassTimeService(masses);
    final weeklyByDay = service.groupWeeklyByDay();
    final upcomingHolidays = service.getUpcomingHolidayMasses(days: 14);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Upcoming holiday masses section
        if (upcomingHolidays.isNotEmpty) ...[
          const Text(
            'Special Masses',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 8),
          ...upcomingHolidays.map((mass) => _HolidayMassTile(mass: mass)),
          const SizedBox(height: 16),
        ],

        // Weekly schedule
        const Text(
          'Weekly Schedule',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        ...weeklyByDay.entries.map((entry) => _DaySchedule(
              day: entry.key,
              masses: entry.value,
            )),
      ],
    );
  }
}

class _HolidayMassTile extends StatelessWidget {
  final MassTime mass;

  const _HolidayMassTile({required this.mass});

  @override
  Widget build(BuildContext context) {
    return Card(
      color: Colors.amber.shade50,
      child: ListTile(
        leading: const Icon(Icons.star, color: Colors.amber),
        title: Text(mass.notes ?? 'Special Mass'),
        subtitle: Text('${mass.formattedDate} at ${mass.formattedTime}'),
        trailing: mass.language != null
            ? Chip(label: Text(mass.language!))
            : null,
      ),
    );
  }
}

class _DaySchedule extends StatelessWidget {
  final String day;
  final List<MassTime> masses;

  const _DaySchedule({required this.day, required this.masses});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 100,
            child: Text(
              day,
              style: const TextStyle(fontWeight: FontWeight.w500),
            ),
          ),
          Expanded(
            child: Wrap(
              spacing: 8,
              runSpacing: 4,
              children: masses.map((mass) {
                final label = mass.language != null
                    ? '${mass.formattedTime} (${mass.language})'
                    : mass.formattedTime;
                return Chip(label: Text(label));
              }).toList(),
            ),
          ),
        ],
      ),
    );
  }
}
*/
